// NOTICE: The transport-neutral registration point is adapted from
// `https://github.com/proj-airi/kbv-extractor-memes/blob/73329d340faf18834990b64b2d17d222e43bb8ea/pkg/grpcpkg/register.go#L1-L60`.
package grpcpkg

import "google.golang.org/grpc/reflection"

type GRPCServiceRegister func(reflection.GRPCServer)

type Register struct {
	httpHandlers []HTTPHandler
	grpcServices []GRPCServiceRegister
}

func NewRegister() *Register {
	return &Register{}
}

func (register *Register) HTTPHandlers() []HTTPHandler {
	return register.httpHandlers
}

func (register *Register) GRPCServices() []GRPCServiceRegister {
	return register.grpcServices
}

func (register *Register) RegisterHTTPHandler(handler HTTPHandler) {
	register.httpHandlers = append(register.httpHandlers, handler)
}

func (register *Register) RegisterGRPCService(service GRPCServiceRegister) {
	register.grpcServices = append(register.grpcServices, service)
}

func (register *Register) RegisterReflection() {
	register.RegisterGRPCService(func(server reflection.GRPCServer) { reflection.Register(server) })
}
