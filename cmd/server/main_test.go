package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/koshuang/personal-agent-runtime/internal/task"
)

func newTestServer(t *testing.T, dbPath string) (*server, func()) {
	t.Helper()
	store, err := task.Open(dbPath)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	return &server{tasks: task.NewService(store)}, func() { _ = store.Close() }
}

func testMux(s *server) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.health)
	mux.HandleFunc("POST /v1/tasks", s.createTask)
	mux.HandleFunc("GET /v1/tasks/{id}", s.getTask)
	mux.HandleFunc("GET /v1/tasks/{id}/result", s.getResult)
	mux.HandleFunc("POST /v1/tasks/{id}/cancel", s.cancelTask)
	return mux
}

func TestHTTPServerTimeouts(t *testing.T) {
	h := http.NewServeMux()
	srv := newHTTPServer(":0", h)
	if srv.ReadHeaderTimeout <= 0 || srv.ReadTimeout <= 0 || srv.WriteTimeout <= 0 || srv.IdleTimeout <= 0 {
		t.Fatalf("all HTTP server timeouts must be finite: %+v", srv)
	}
	if srv.Handler != h {
		t.Fatal("handler wiring changed")
	}
}

func TestHealth(t *testing.T) {
	db := filepath.Join(t.TempDir(), "runtime.db")
	s, closeFn := newTestServer(t, db)
	defer closeFn()

	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	res := httptest.NewRecorder()
	testMux(s).ServeHTTP(res, req)

	if res.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", res.Code, http.StatusOK)
	}
}

func TestCreateGetCancelTask(t *testing.T) {
	db := filepath.Join(t.TempDir(), "runtime.db")
	s, closeFn := newTestServer(t, db)
	defer closeFn()
	mux := testMux(s)

	body := bytes.NewBufferString(`{"prompt":"test api behavior"}`)
	createReq := httptest.NewRequest(http.MethodPost, "/v1/tasks", body)
	createReq.Header.Set("Content-Type", "application/json")
	createRes := httptest.NewRecorder()
	mux.ServeHTTP(createRes, createReq)

	if createRes.Code != http.StatusAccepted {
		t.Fatalf("create status = %d, want %d: %s", createRes.Code, http.StatusAccepted, createRes.Body.String())
	}

	var created struct {
		TaskID string `json:"task_id"`
		Status string `json:"status"`
	}
	if err := json.NewDecoder(createRes.Body).Decode(&created); err != nil {
		t.Fatalf("decode create response: %v", err)
	}
	if created.TaskID == "" || created.Status != "queued" {
		t.Fatalf("unexpected create response: %+v", created)
	}

	getReq := httptest.NewRequest(http.MethodGet, "/v1/tasks/"+created.TaskID, nil)
	getRes := httptest.NewRecorder()
	mux.ServeHTTP(getRes, getReq)
	if getRes.Code != http.StatusOK {
		t.Fatalf("get status = %d, want %d: %s", getRes.Code, http.StatusOK, getRes.Body.String())
	}

	var got task.Task
	if err := json.NewDecoder(getRes.Body).Decode(&got); err != nil {
		t.Fatalf("decode get response: %v", err)
	}
	if got.ID != created.TaskID || got.Status != "queued" || got.Stage != "queued" || got.Progress != 0 {
		t.Fatalf("unexpected task: %+v", got)
	}

	resultReq := httptest.NewRequest(http.MethodGet, "/v1/tasks/"+created.TaskID+"/result", nil)
	resultRes := httptest.NewRecorder()
	mux.ServeHTTP(resultRes, resultReq)
	if resultRes.Code != http.StatusConflict {
		t.Fatalf("result status = %d, want %d: %s", resultRes.Code, http.StatusConflict, resultRes.Body.String())
	}

	cancelReq := httptest.NewRequest(http.MethodPost, "/v1/tasks/"+created.TaskID+"/cancel", nil)
	cancelRes := httptest.NewRecorder()
	mux.ServeHTTP(cancelRes, cancelReq)
	if cancelRes.Code != http.StatusOK {
		t.Fatalf("cancel status = %d, want %d: %s", cancelRes.Code, http.StatusOK, cancelRes.Body.String())
	}

	secondCancelReq := httptest.NewRequest(http.MethodPost, "/v1/tasks/"+created.TaskID+"/cancel", nil)
	secondCancelRes := httptest.NewRecorder()
	mux.ServeHTTP(secondCancelRes, secondCancelReq)
	if secondCancelRes.Code != http.StatusConflict {
		t.Fatalf("second cancel status = %d, want %d: %s", secondCancelRes.Code, http.StatusConflict, secondCancelRes.Body.String())
	}

	getCanceledReq := httptest.NewRequest(http.MethodGet, "/v1/tasks/"+created.TaskID, nil)
	getCanceledRes := httptest.NewRecorder()
	mux.ServeHTTP(getCanceledRes, getCanceledReq)
	if getCanceledRes.Code != http.StatusOK {
		t.Fatalf("get canceled status = %d: %s", getCanceledRes.Code, getCanceledRes.Body.String())
	}
	if err := json.NewDecoder(getCanceledRes.Body).Decode(&got); err != nil {
		t.Fatalf("decode canceled task: %v", err)
	}
	if got.Status != "canceled" || got.Stage != "canceled" {
		t.Fatalf("unexpected canceled task: %+v", got)
	}
}

func TestTaskPersistsAcrossStoreReopen(t *testing.T) {
	db := filepath.Join(t.TempDir(), "runtime.db")
	s1, close1 := newTestServer(t, db)
	mux1 := testMux(s1)

	createReq := httptest.NewRequest(http.MethodPost, "/v1/tasks", bytes.NewBufferString(`{"prompt":"persist me"}`))
	createReq.Header.Set("Content-Type", "application/json")
	createRes := httptest.NewRecorder()
	mux1.ServeHTTP(createRes, createReq)
	if createRes.Code != http.StatusAccepted {
		t.Fatalf("create status = %d: %s", createRes.Code, createRes.Body.String())
	}

	var created struct {
		TaskID string `json:"task_id"`
	}
	if err := json.NewDecoder(createRes.Body).Decode(&created); err != nil {
		t.Fatalf("decode create response: %v", err)
	}
	close1()

	time.Sleep(10 * time.Millisecond)
	s2, close2 := newTestServer(t, db)
	defer close2()
	mux2 := testMux(s2)

	getReq := httptest.NewRequest(http.MethodGet, "/v1/tasks/"+created.TaskID, nil)
	getRes := httptest.NewRecorder()
	mux2.ServeHTTP(getRes, getReq)
	if getRes.Code != http.StatusOK {
		t.Fatalf("get after reopen status = %d, want %d: %s", getRes.Code, http.StatusOK, getRes.Body.String())
	}
}

func TestCreateTaskValidationAndNotFound(t *testing.T) {
	db := filepath.Join(t.TempDir(), "runtime.db")
	s, closeFn := newTestServer(t, db)
	defer closeFn()
	mux := testMux(s)

	badReq := httptest.NewRequest(http.MethodPost, "/v1/tasks", bytes.NewBufferString(`{"prompt":"   "}`))
	badRes := httptest.NewRecorder()
	mux.ServeHTTP(badRes, badReq)
	if badRes.Code != http.StatusBadRequest {
		t.Fatalf("blank prompt status = %d, want %d", badRes.Code, http.StatusBadRequest)
	}

	oversizedPrompt := `{"prompt":"` + strings.Repeat("x", maxPromptBytes+1) + `"}`
	oversizedPromptReq := httptest.NewRequest(http.MethodPost, "/v1/tasks", strings.NewReader(oversizedPrompt))
	oversizedPromptRes := httptest.NewRecorder()
	mux.ServeHTTP(oversizedPromptRes, oversizedPromptReq)
	if oversizedPromptRes.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversized prompt status = %d, want %d", oversizedPromptRes.Code, http.StatusRequestEntityTooLarge)
	}

	oversizedBody := `{"prompt":"` + strings.Repeat("x", maxTaskRequestBytes) + `"}`
	oversizedBodyReq := httptest.NewRequest(http.MethodPost, "/v1/tasks", strings.NewReader(oversizedBody))
	oversizedBodyRes := httptest.NewRecorder()
	mux.ServeHTTP(oversizedBodyRes, oversizedBodyReq)
	if oversizedBodyRes.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversized body status = %d, want %d", oversizedBodyRes.Code, http.StatusRequestEntityTooLarge)
	}

	missingReq := httptest.NewRequest(http.MethodGet, "/v1/tasks/tsk_missing", nil)
	missingRes := httptest.NewRecorder()
	mux.ServeHTTP(missingRes, missingReq)
	if missingRes.Code != http.StatusNotFound {
		t.Fatalf("missing task status = %d, want %d", missingRes.Code, http.StatusNotFound)
	}

	missingCancelReq := httptest.NewRequest(http.MethodPost, "/v1/tasks/tsk_missing/cancel", nil)
	missingCancelRes := httptest.NewRecorder()
	mux.ServeHTTP(missingCancelRes, missingCancelReq)
	if missingCancelRes.Code != http.StatusNotFound {
		t.Fatalf("missing cancel status = %d, want %d", missingCancelRes.Code, http.StatusNotFound)
	}
}
