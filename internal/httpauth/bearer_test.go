package httpauth

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestBearerAllowsValidToken(t *testing.T) {
	called := false
	h := Bearer("secret", http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		called = true
		w.WriteHeader(http.StatusNoContent)
	}))
	req := httptest.NewRequest(http.MethodPost, "/mcp", nil)
	req.Header.Set("Authorization", "Bearer secret")
	res := httptest.NewRecorder()
	h.ServeHTTP(res, req)
	if res.Code != http.StatusNoContent || !called {
		t.Fatalf("status=%d called=%v", res.Code, called)
	}
}

func TestBearerRejectsMissingOrWrongToken(t *testing.T) {
	h := Bearer("secret", http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Fatal("protected handler must not be called")
	}))
	for _, auth := range []string{"", "Bearer wrong", "Basic secret", "Bearer"} {
		req := httptest.NewRequest(http.MethodPost, "/mcp", nil)
		if auth != "" {
			req.Header.Set("Authorization", auth)
		}
		res := httptest.NewRecorder()
		h.ServeHTTP(res, req)
		if res.Code != http.StatusUnauthorized {
			t.Fatalf("auth=%q status=%d want=%d", auth, res.Code, http.StatusUnauthorized)
		}
		if res.Header().Get("WWW-Authenticate") == "" {
			t.Fatalf("auth=%q missing WWW-Authenticate", auth)
		}
	}
}

func TestBearerEmptyTokenLeavesLoopbackDevelopmentUnauthenticated(t *testing.T) {
	called := false
	h := Bearer("", http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		called = true
		w.WriteHeader(http.StatusNoContent)
	}))
	res := httptest.NewRecorder()
	h.ServeHTTP(res, httptest.NewRequest(http.MethodPost, "/mcp", nil))
	if res.Code != http.StatusNoContent || !called {
		t.Fatalf("status=%d called=%v", res.Code, called)
	}
}

func TestIsLoopbackAddress(t *testing.T) {
	for _, addr := range []string{"127.0.0.1:8080", "[::1]:8080", "localhost:8080"} {
		if !IsLoopbackAddress(addr) {
			t.Fatalf("%q should be loopback", addr)
		}
	}
	for _, addr := range []string{"0.0.0.0:8080", ":8080", "192.168.1.10:8080", "example.com:8080", "bad"} {
		if IsLoopbackAddress(addr) {
			t.Fatalf("%q should not be loopback", addr)
		}
	}
}
