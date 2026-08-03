package grpcservers

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"time"

	"buf.build/go/protovalidate"
	"github.com/labstack/echo/v5"
	"github.com/labstack/echo/v5/middleware"
	"github.com/proj-airi/recorder-minecraft/internal/grpc/servers/interceptors"
	servermiddleware "github.com/proj-airi/recorder-minecraft/internal/grpc/servers/middlewares"
	grpcservices "github.com/proj-airi/recorder-minecraft/internal/grpc/services"
	"github.com/proj-airi/recorder-minecraft/internal/models/catalog"
	"github.com/proj-airi/recorder-minecraft/pkg/grpcpkg"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

type Options struct {
	GRPCAddress string
	HTTPAddress string
}

type Server struct {
	artifactsRoot string
	options       Options
	register      *grpcpkg.Register
}

func New(catalog *catalog.Service, options Options) *Server {
	return newServer(catalog.Root(), options, grpcservices.Register(catalog))
}

func newServer(artifactsRoot string, options Options, register *grpcpkg.Register) *Server {
	return &Server{artifactsRoot: artifactsRoot, options: options, register: register}
}

func (server *Server) Serve(ctx context.Context) error {
	validator, err := protovalidate.New()
	if err != nil {
		return fmt.Errorf("create request validator: %w", err)
	}
	grpcServer := grpc.NewServer(grpc.ChainUnaryInterceptor(interceptors.Panic(), interceptors.Validate(validator)))
	for _, register := range server.register.GRPCServices() {
		register(grpcServer)
	}
	grpcListener, err := net.Listen("tcp", server.options.GRPCAddress)
	if err != nil {
		return fmt.Errorf("listen for gRPC on %s: %w", server.options.GRPCAddress, err)
	}

	connection, err := grpc.NewClient(grpcListener.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		_ = grpcListener.Close()
		return fmt.Errorf("create gateway connection: %w", err)
	}
	defer func() { _ = connection.Close() }()
	gateway, err := grpcpkg.NewGateway(connection, server.register.HTTPHandlers()...)
	if err != nil {
		_ = grpcListener.Close()
		return fmt.Errorf("create gRPC gateway: %w", err)
	}
	assetRoot, err := os.OpenRoot(server.artifactsRoot)
	if err != nil {
		_ = grpcListener.Close()
		return fmt.Errorf("open artifact root: %w", err)
	}
	defer func() { _ = assetRoot.Close() }()

	echoServer := echo.New()
	echoServer.Use(middleware.CORSWithConfig(middleware.CORSConfig{
		AllowOrigins:  []string{"*"},
		AllowMethods:  []string{http.MethodGet, http.MethodHead, http.MethodOptions},
		AllowHeaders:  []string{echo.HeaderAccept, echo.HeaderContentType, "Range"},
		ExposeHeaders: []string{"Accept-Ranges", echo.HeaderContentLength, "Content-Range"},
	}))
	echoServer.Any("/api/*", echo.WrapHandler(gateway))
	assetsMiddleware := servermiddleware.Assets(assetRoot.FS())
	notFound := func(*echo.Context) error { return echo.ErrNotFound }
	echoServer.GET("/assets/*", notFound, assetsMiddleware)
	echoServer.HEAD("/assets/*", notFound, assetsMiddleware)
	echoServer.GET("/healthz", func(context *echo.Context) error { return context.NoContent(http.StatusNoContent) })
	httpServer := &http.Server{Addr: server.options.HTTPAddress, Handler: echoServer, ReadHeaderTimeout: 10 * time.Second}
	httpListener, err := net.Listen("tcp", server.options.HTTPAddress)
	if err != nil {
		_ = grpcListener.Close()
		return fmt.Errorf("listen for HTTP on %s: %w", server.options.HTTPAddress, err)
	}

	errorsChannel := make(chan error, 2)
	go func() {
		if err := grpcServer.Serve(grpcListener); err != nil && !errors.Is(err, grpc.ErrServerStopped) {
			errorsChannel <- fmt.Errorf("serve gRPC: %w", err)
		}
	}()
	go func() {
		if err := httpServer.Serve(httpListener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errorsChannel <- fmt.Errorf("serve HTTP gateway: %w", err)
		}
	}()

	select {
	case err := <-errorsChannel:
		grpcServer.Stop()
		_ = httpServer.Close()
		return err
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		grpcServer.GracefulStop()
		if err := httpServer.Shutdown(shutdownCtx); err != nil {
			return fmt.Errorf("shut down HTTP gateway: %w", err)
		}
		return nil
	}
}
