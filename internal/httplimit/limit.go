package httplimit

import (
	"fmt"
	"math"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// Limiter is a process-local token bucket intended as a defense-in-depth
// boundary behind authentication. A trusted public gateway should still apply
// its own distributed rate limits before proxying to the runtime.
type Limiter struct {
	mu       sync.Mutex
	rate     float64
	capacity float64
	tokens   float64
	last     time.Time
	now      func() time.Time
}

// New creates a token bucket with requestsPerMinute refill rate and burst
// capacity. Both values must be positive.
func New(requestsPerMinute, burst int) (*Limiter, error) {
	return newWithClock(requestsPerMinute, burst, time.Now)
}

func newWithClock(requestsPerMinute, burst int, now func() time.Time) (*Limiter, error) {
	if requestsPerMinute <= 0 {
		return nil, fmt.Errorf("requests per minute must be positive")
	}
	if burst <= 0 {
		return nil, fmt.Errorf("burst must be positive")
	}
	if now == nil {
		return nil, fmt.Errorf("clock is required")
	}
	current := now()
	return &Limiter{
		rate:     float64(requestsPerMinute) / 60.0,
		capacity: float64(burst),
		tokens:   float64(burst),
		last:     current,
		now:      now,
	}, nil
}

// Middleware rejects requests with HTTP 429 once the bucket is empty. CORS
// preflight is intentionally not charged because it is unauthenticated by the
// bearer boundary and performs no application action.
func (l *Limiter) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodOptions {
			next.ServeHTTP(w, r)
			return
		}

		allowed, remaining, retryAfter := l.allow()
		w.Header().Set("RateLimit-Limit", strconv.Itoa(int(l.capacity)))
		w.Header().Set("RateLimit-Remaining", strconv.Itoa(remaining))
		if !allowed {
			seconds := int(math.Ceil(retryAfter.Seconds()))
			if seconds < 1 {
				seconds = 1
			}
			w.Header().Set("Retry-After", strconv.Itoa(seconds))
			http.Error(w, http.StatusText(http.StatusTooManyRequests), http.StatusTooManyRequests)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (l *Limiter) allow() (bool, int, time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()

	now := l.now()
	elapsed := now.Sub(l.last).Seconds()
	if elapsed > 0 {
		l.tokens = math.Min(l.capacity, l.tokens+elapsed*l.rate)
		l.last = now
	}

	if l.tokens >= 1 {
		l.tokens--
		return true, int(math.Floor(l.tokens)), 0
	}

	missing := 1 - l.tokens
	retryAfter := time.Duration((missing / l.rate) * float64(time.Second))
	return false, 0, retryAfter
}
