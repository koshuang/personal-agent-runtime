package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/koshuang/personal-agent-runtime/internal/task"
)

type server struct{ store *task.Store }

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

	s := &server{store: store}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.health)
	mux.HandleFunc("POST /v1/tasks", s.createTask)
	mux.HandleFunc("GET /v1/tasks/{id}", s.getTask)
	mux.HandleFunc("GET /v1/tasks/{id}/result", s.getResult)
	mux.HandleFunc("POST /v1/tasks/{id}/cancel", s.cancelTask)

	addr := env("PAR_ADDR", ":8080")
	log.Printf("personal-agent-runtime API listening on %s (db=%s)", addr, dbPath)
	log.Fatal(http.ListenAndServe(addr, mux))
}

func (s *server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok"})
}

func (s *server) createTask(w http.ResponseWriter, r *http.Request) {
	var req createTaskRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || strings.TrimSpace(req.Prompt) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "prompt is required"})
		return
	}
	now := time.Now().UTC()
	t := task.Task{ID: newID(), Prompt: req.Prompt, Status: "queued", Stage: "queued", Progress: 0, CreatedAt: now, UpdatedAt: now}
	if err := s.store.Create(r.Context(), t); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"task_id": t.ID, "status": t.Status})
}

func (s *server) getTask(w http.ResponseWriter, r *http.Request) {
	t, err := s.store.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, t)
}

func (s *server) getResult(w http.ResponseWriter, r *http.Request) {
	t, err := s.store.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	if t.Status != "completed" || t.Result == nil {
		writeJSON(w, http.StatusConflict, map[string]any{"task_id": t.ID, "status": t.Status, "error": "result not ready"})
		return
	}
	var result any
	if err := json.Unmarshal([]byte(*t.Result), &result); err != nil {
		result = *t.Result
	}
	writeJSON(w, http.StatusOK, map[string]any{"task_id": t.ID, "status": t.Status, "result": result})
}

func (s *server) cancelTask(w http.ResponseWriter, r *http.Request) {
	t, err := s.store.Get(r.Context(), r.PathValue("id"))
	if err != nil {
		writeStoreError(w, err)
		return
	}
	if t.Status == "completed" || t.Status == "failed" || t.Status == "canceled" {
		writeJSON(w, http.StatusConflict, map[string]any{"task_id": t.ID, "status": t.Status, "error": "task is already terminal"})
		return
	}
	if err := s.store.UpdateState(r.Context(), t.ID, "canceled", "canceled", t.Progress, t.Result); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"task_id": t.ID, "status": "canceled"})
}

func newID() string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return "tsk_" + hex.EncodeToString(b)
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func writeStoreError(w http.ResponseWriter, err error) {
	if errors.Is(err, os.ErrNotExist) || strings.Contains(err.Error(), "not found") {
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
