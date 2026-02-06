package main

import (
	"context"
	"log"
	"os/signal"
	"syscall"
	"time"

	"github.com/zohaibsaeed93/django-ops/agent/internal/config"
	"github.com/zohaibsaeed93/django-ops/agent/internal/telemetry"
	"github.com/zohaibsaeed93/django-ops/agent/internal/transport"
)

func main() {
	cfg, err := config.Load()
	if err != nil {
		log.Fatal(err)
	}
	recorder, err := telemetry.New(cfg.OTLPEndpoint, 128)
	if err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	started := time.Now()
	runner := &transport.Runner{Config: cfg}
	err = runner.Run(ctx)
	outcome := "succeeded"
	if err != nil {
		outcome = "failed"
	}
	recorder.Observe("connection", outcome, cfg.AgentID, time.Since(started))
	if err != nil {
		log.Fatal(err)
	}
}
