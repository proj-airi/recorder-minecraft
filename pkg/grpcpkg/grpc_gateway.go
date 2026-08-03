// NOTICE: The gateway constructor is adapted from
// `https://github.com/proj-airi/kbv-extractor-memes/blob/73329d340faf18834990b64b2d17d222e43bb8ea/pkg/grpcpkg/grpc_gateway.go#L1-L52`.
package grpcpkg

import (
	"context"
	"net/http"

	"github.com/grpc-ecosystem/grpc-gateway/v2/runtime"
	"google.golang.org/grpc"
)

type HTTPHandler func(context.Context, *runtime.ServeMux, *grpc.ClientConn) error

func NewGateway(conn *grpc.ClientConn, handlers ...HTTPHandler) (http.Handler, error) {
	mux := runtime.NewServeMux()
	for _, handler := range handlers {
		if err := handler(context.Background(), mux, conn); err != nil {
			return nil, err
		}
	}
	return mux, nil
}
