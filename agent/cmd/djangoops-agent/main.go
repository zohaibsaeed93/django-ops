package main

import (
	"context"
	"log"
	"os/signal"
	"syscall"

	"github.com/zohaibsaeed93/django-ops/agent/internal/config"
	"github.com/zohaibsaeed93/django-ops/agent/internal/transport"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	runner := &transport.Runner{Config: cfg}
	if err := runner.Run(ctx); err != nil {
		log.Fatal(err)
	}
}
