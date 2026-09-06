package httpauth

import (
	"crypto/subtle"
	"net"
	"net/http"
	"strings"
)

// Bearer protects an HTTP handler with a static bearer token. An empty token
// disables authentication so loopback-only development remains frictionless.
func Bearer(token string, next http.Handler) http.Handler {
	token = strings.TrimSpace(token)
	if token == "" {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		provided := bearerToken(r.Header.Get("Authorization"))
		if !constantTimeEqual(provided, token) {
			w.Header().Set("WWW-Authenticate", `Bearer realm="personal-agent-runtime"`)
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func bearerToken(header string) string {
	parts := strings.Fields(header)
	if len(parts) != 2 || !strings.EqualFold(parts[0], "Bearer") {
		return ""
	}
	return parts[1]
}

func constantTimeEqual(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}

// IsLoopbackAddress reports whether an HTTP listen address is confined to
// loopback. Hostnames other than localhost are treated as non-loopback.
func IsLoopbackAddress(addr string) bool {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return false
	}
	host = strings.TrimSpace(host)
	if strings.EqualFold(host, "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}
