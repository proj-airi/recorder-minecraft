package interceptors

import (
	"context"
	"fmt"

	"buf.build/go/protovalidate"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

func Validate(validator protovalidate.Validator) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, request any, _ *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		message, ok := request.(proto.Message)
		if !ok {
			return nil, status.Error(codes.Internal, "request is not a protobuf message")
		}
		if err := validator.Validate(message); err != nil {
			return nil, status.Error(codes.InvalidArgument, fmt.Sprintf("invalid request: %v", err))
		}
		return handler(ctx, request)
	}
}
