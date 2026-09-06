package mcpserver

import (
	"encoding/json"
	"net/http"
)

const maxMCPRequestBytes = 64 * 1024

func writeHTTPJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
