package execution

import (
	"context"
	"errors"
	"fmt"
)

var ErrNoAffordableWorker = errors.New("no worker is allowed by the current cost policy")

type RouteCandidate struct {
	Name             string
	Worker           Worker
	EstimatedCostUSD float64
	CanHandle        func(WorkerInput) bool
}

type Router struct {
	maxCostUSD float64
	candidates []RouteCandidate
}

func NewRouter(maxCostUSD float64, candidates ...RouteCandidate) (*Router, error) {
	if maxCostUSD < 0 {
		return nil, errors.New("max cost must be non-negative")
	}
	if len(candidates) == 0 {
		return nil, errors.New("at least one route candidate is required")
	}
	for _, candidate := range candidates {
		if candidate.Name == "" || candidate.Worker == nil {
			return nil, errors.New("route candidate requires name and worker")
		}
		if candidate.EstimatedCostUSD < 0 {
			return nil, errors.New("route candidate cost must be non-negative")
		}
	}
	return &Router{maxCostUSD: maxCostUSD, candidates: append([]RouteCandidate(nil), candidates...)}, nil
}

func (r *Router) Run(ctx context.Context, input WorkerInput) (WorkerResult, error) {
	for _, candidate := range r.candidates {
		if candidate.EstimatedCostUSD > r.maxCostUSD {
			continue
		}
		if candidate.CanHandle != nil && !candidate.CanHandle(input) {
			continue
		}

		result, err := candidate.Worker.Run(ctx, input)
		if err != nil {
			return WorkerResult{}, err
		}
		if result.Checks == nil {
			result.Checks = map[string]any{}
		}
		result.Checks["route_worker"] = candidate.Name
		result.Checks["route_estimated_cost_usd"] = candidate.EstimatedCostUSD
		result.Checks["max_cost_usd"] = r.maxCostUSD
		return result, nil
	}
	return WorkerResult{}, fmt.Errorf("%w: max_cost_usd=%g", ErrNoAffordableWorker, r.maxCostUSD)
}
