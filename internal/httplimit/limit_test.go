package httplimit

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestLimiterRejectsAfterBurstAndRefills(t *testing.T) {
	now := time.Unix(0, 0)
	limiter, err := newWithClock(60, 2, func() time.Time { return now })
	if err != nil {
		t.Fatalf("new limiter: %v", err)
	}
	calls := 0
	handler := limiter.Middleware(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		w.WriteHeader(http.StatusNoContent)
	}))

	for i := 0; i < 2; i++ {
		res := httptest.NewRecorder()
		handler.ServeHTTP(res, httptest.NewRequest(http.MethodPost, "/mcp", nil))
		if res.Code != http.StatusNoContent {
			t.Fatalf("request %d status=%d", i+1, res.Code)
		}
	}

	limited := httptest.NewRecorder()
	handler.ServeHTTP(limited, httptest.NewRequest(http.MethodPost, "/mcp", nil))
	if limited.Code != http.StatusTooManyRequests {
		t.Fatalf("limited status=%d", limited.Code)
	}
	if limited.Header().Get("Retry-After") != "1" {
		t.Fatalf("retry-after=%q", limited.Header().Get("Retry-After"))
	}
	if calls != 2 {
		t.Fatalf("downstream calls=%d", calls)
	}

	now = now.Add(time.Second)
	refilled := httptest.NewRecorder()
	handler.ServeHTTP(refilled, httptest.NewRequest(http.MethodPost, "/mcp", nil))
	if refilled.Code != http.StatusNoContent {
		t.Fatalf("refilled status=%d", refilled.Code)
	}
	if calls != 3 {
		t.Fatalf("downstream calls after refill=%d", calls)
	}
}

func TestPreflightDoesNotConsumeQuota(t *testing.T) {
	now := time.Unix(0, 0)
	limiter, err := newWithClock(60, 1, func() time.Time { return now })
	if err != nil {
		t.Fatalf("new limiter: %v", err)
	}
	calls := 0
	handler := limiter.Middleware(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		calls++
		w.WriteHeader(http.StatusNoContent)
	}))

	preflight := httptest.NewRecorder()
	handler.ServeHTTP(preflight, httptest.NewRequest(http.MethodOptions, "/mcp", nil))
	if preflight.Code != http.StatusNoContent {
		t.Fatalf("preflight status=%d", preflight.Code)
	}

	firstAction := httptest.NewRecorder()
	handler.ServeHTTP(firstAction, httptest.NewRequest(http.MethodPost, "/mcp", nil))
	if firstAction.Code != http.StatusNoContent {
		t.Fatalf("first action status=%d", firstAction.Code)
	}

	secondAction := httptest.NewRecorder()
	handler.ServeHTTP(secondAction, httptest.NewRequest(http.MethodPost, "/mcp", nil))
	if secondAction.Code != http.StatusTooManyRequests {
		t.Fatalf("second action status=%d", secondAction.Code)
	}
	if calls != 2 {
		t.Fatalf("downstream calls=%d want 2 (preflight + first action)", calls)
	}
}

func TestNewRejectsInvalidConfiguration(t *testing.T) {
	if _, err := New(0, 1); err == nil {
		t.Fatal("expected invalid requests-per-minute error")
	}
	if _, err := New(1, 0); err == nil {
		t.Fatal("expected invalid burst error")
	}
}
