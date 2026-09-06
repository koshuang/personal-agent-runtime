package main

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/koshuang/personal-agent-runtime/internal/mcpserver"
	"github.com/koshuang/personal-agent-runtime/internal/task"
)

const maxTaskRequestBytes = 32 * 1024

type server struct{ tasks *task.Service }

type createTaskRequest struct {
	Prompt string `json:"prompt"`
}

func main() {
	dbPath := env("PAR_DB", ".par/runtime-go.db")
	if err := os.MkdirAll(filepath.Dir(dbPath), 0o755); err != nil {
		log.Fatal(err)
	}
	store, err := task.Open(dbPath)
	if err != nil {
		log.Fatal(err)
	}
	defer store.Close()

	tasks := task.NewService(store)
	s := &server{tasks: tasks}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.health)
	mux.HandleFunc("POST /v1/tasks", s.createTask)
	mux.HandleFunc("GET /v1/tasks/{id}", s.getTask)
	mux.HandleFunc("GET /v1/tasks/{id}/result", s.getResult)
	mux.HandleFunc("POST /v1/tasks/{id}/cancel", s.cancelTask)
	mux.Handle("/mcp", mcpserver.New(tasks))

	addr := env("PAR_ADDR", "127.0.0.1:8080")
	log.Printf("personal-agent-runtime API listening on %s (db=%s, mcp=/mcp)", addr, dbPath)
	httpServer := newHTTPServer(addr, mux)
	log.Fatal(httpServer.ListenAndServe())
}

func newHTTPServer(addr string, handler http.Handler) *http.Server {
	return &http.Server{
		Addr:              addr,
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
}

func (s *server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "mcp": "/mcp"})
}

func (s *server) createTask(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxTaskRequestBytes)
	var req createTaskRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		var maxBytesErr *http.MaxBytesError
		if errors.As(err, &maxBytesErr) {
			writeJSON(w, http.StatusRequestEntityTooLarge, map[string]any{"error": "request body too large"})
			return
		}
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "invalid request body"})
		return
	}
	if strings.TrimSpace(req.Prompt) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "prompt is required"})
		return
	}
	if len(strings.TrimSpace(req.Prompt)) > task.MaxPromptBytes {
		writeJSON(w, http.StatusRequestEntityTooLarge, map[string]any{"error": task.ErrPromptTooLarge.Error()})
		return
	}
	t, err := s.tasks.Create(r.Context(), req.Prompt)
	if err != nil {
		if errors.Is(err, task.ErrPromptTooLarge) {
			writeJSON(w, http.StatusRequestEntityTooLarge, map[string]any{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"task_id": t.ID, "status": t.Status})
}

func (s *server) getTask(w http.ResponseWriter, r *http.Request) {
	t, err := s.tasks.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, t)
}

func (s *server) getResult(w http.ResponseWriter, r *http.Request) {
	t, err := s.tasks.Result(r.Context(), r.PathValue("id"))
	if err != nil {
		if errors.Is(err, task.ErrResultNotReady) {
			writeJSON(w, http.StatusConflict, map[string]any{"task_id": t.ID, "status": t.Status, "error": "result not ready"})
			return
		}
		writeStoreError(w, err)
		return
	}
	var result any
	if err := json.Unmarshal([]byte(*t.Result), &result); err != nil {
		result = *t.Result
	}
	writeJSON(w, http.StatusOK, map[string]any{"task_id": t.ID, "status": t.Status, "result": result})
}

func (s *server) cancelTask(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if err := s.tasks.Cancel(r.Context(), id); err != nil {
		switch {
		case errors.Is(err, task.ErrNotFound):
			writeJSON(w, http.StatusNotFound, map[string]any{"error": "task not found"})
		case errors.Is(err, task.ErrTerminal):
			writeJSON(w, http.StatusConflict, map[string]any{"task_id": id, "error": "task is already terminal"})
		default:
			writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		}
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"task_id": id, "status": "canceled"})
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func writeStoreError(w http.ResponseWriter, err error) {
	if errors.Is(err, task.ErrNotFound) || errors.Is(err, os.ErrNotExist) || strings.Contains(err.Error(), "not found") {
		writeJSON(w, http.StatusNotFound, map[string]any{"error": "task not found"})
		return
	}
	writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
