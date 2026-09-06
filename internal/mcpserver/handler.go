package mcpserver

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/koshuang/personal-agent-runtime/internal/task"
)

const maxMCPRequestBytes = 64 * 1024

const serverInstructions = "Use submit_task to queue work, then get_task to inspect status and get_task_result only after completion. Use cancel_task only when the user explicitly wants to stop a task. Task execution is asynchronous and persisted across server restarts."

type Handler struct {
	tasks *task.Service
}

type rpcRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type rpcResponse struct {
	JSONRPC string    `json:"jsonrpc"`
	ID      any       `json:"id,omitempty"`
	Result  any       `json:"result,omitempty"`
	Error   *rpcError `json:"error,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type initializeParams struct {
	ProtocolVersion string `json:"protocolVersion"`
}

type callToolParams struct {
	Name      string          `json:"name"`
	Arguments json.RawMessage `json:"arguments,omitempty"`
}

type promptArgs struct {
	Prompt string `json:"prompt"`
}

type taskIDArgs struct {
	TaskID string `json:"task_id"`
}

func New(tasks *task.Service) http.Handler {
	return &Handler{tasks: tasks}
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Accept, Mcp-Protocol-Version, Mcp-Session-Id")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST, OPTIONS")
		writeHTTPJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "MCP endpoint accepts POST requests"})
		return
	}
	if ct := r.Header.Get("Content-Type"); ct != "" && !strings.HasPrefix(strings.ToLower(ct), "application/json") {
		writeHTTPJSON(w, http.StatusUnsupportedMediaType, map[string]any{"error": "content-type must be application/json"})
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, maxMCPRequestBytes)
	dec := json.NewDecoder(r.Body)
	var req rpcRequest
	if err := dec.Decode(&req); err != nil {
		if errors.As(err, new(*http.MaxBytesError)) {
			writeHTTPJSON(w, http.StatusRequestEntityTooLarge, rpcResponse{JSONRPC: "2.0", ID: nil, Error: &rpcError{Code: -32700, Message: "request too large"}})
			return
		}
		writeHTTPJSON(w, http.StatusBadRequest, rpcResponse{JSONRPC: "2.0", ID: nil, Error: &rpcError{Code: -32700, Message: "parse error"}})
		return
	}
	if err := ensureEOF(dec); err != nil {
		writeHTTPJSON(w, http.StatusBadRequest, rpcResponse{JSONRPC: "2.0", ID: decodeID(req.ID), Error: &rpcError{Code: -32600, Message: "invalid request"}})
		return
	}
	if req.JSONRPC != "2.0" || req.Method == "" {
		writeHTTPJSON(w, http.StatusBadRequest, rpcResponse{JSONRPC: "2.0", ID: decodeID(req.ID), Error: &rpcError{Code: -32600, Message: "invalid request"}})
		return
	}

	// Notifications have no id and intentionally receive no JSON-RPC response.
	isNotification := len(req.ID) == 0 || string(req.ID) == "null"
	if isNotification {
		if req.Method == "notifications/initialized" || strings.HasPrefix(req.Method, "notifications/") {
			w.WriteHeader(http.StatusAccepted)
			return
		}
	}

	id := decodeID(req.ID)
	switch req.Method {
	case "initialize":
		var p initializeParams
		if len(req.Params) > 0 {
			_ = json.Unmarshal(req.Params, &p)
		}
		version := negotiateProtocolVersion(p.ProtocolVersion)
		w.Header().Set("Mcp-Protocol-Version", version)
		writeRPCResult(w, id, map[string]any{
			"protocolVersion": version,
			"capabilities": map[string]any{
				"tools": map[string]any{"listChanged": false},
			},
			"serverInfo": map[string]any{
				"name":    "personal-agent-runtime",
				"version": "0.1.0",
			},
			"instructions": serverInstructions,
		})
	case "ping":
		writeRPCResult(w, id, map[string]any{})
	case "tools/list":
		writeRPCResult(w, id, map[string]any{"tools": toolDefinitions()})
	case "tools/call":
		h.callTool(w, r, id, req.Params)
	default:
		writeRPCError(w, id, -32601, "method not found")
	}
}

func (h *Handler) callTool(w http.ResponseWriter, r *http.Request, id any, raw json.RawMessage) {
	var p callToolParams
	if err := json.Unmarshal(raw, &p); err != nil || p.Name == "" {
		writeRPCError(w, id, -32602, "invalid tool call")
		return
	}

	switch p.Name {
	case "submit_task":
		var args promptArgs
		if err := json.Unmarshal(p.Arguments, &args); err != nil || strings.TrimSpace(args.Prompt) == "" {
			writeToolError(w, id, "prompt is required")
			return
		}
		t, err := h.tasks.Create(r.Context(), args.Prompt)
		if err != nil {
			writeToolError(w, id, err.Error())
			return
		}
		writeToolResult(w, id, map[string]any{
			"task_id": t.ID,
			"status":  t.Status,
			"stage":   t.Stage,
		}, fmt.Sprintf("Task %s was queued.", t.ID))

	case "get_task":
		args, ok := decodeTaskIDArgs(p.Arguments)
		if !ok {
			writeToolError(w, id, "task_id is required")
			return
		}
		t, err := h.tasks.Get(r.Context(), args.TaskID)
		if err != nil {
			writeTaskError(w, id, err)
			return
		}
		writeToolResult(w, id, t, fmt.Sprintf("Task %s is %s (%s), progress %d%%.", t.ID, t.Status, t.Stage, t.Progress))

	case "get_task_result":
		args, ok := decodeTaskIDArgs(p.Arguments)
		if !ok {
			writeToolError(w, id, "task_id is required")
			return
		}
		t, err := h.tasks.Result(r.Context(), args.TaskID)
		if err != nil {
			if errors.Is(err, task.ErrResultNotReady) {
				writeToolResult(w, id, map[string]any{"task_id": t.ID, "status": t.Status, "ready": false}, fmt.Sprintf("Task %s result is not ready; current status is %s.", t.ID, t.Status))
				return
			}
			writeTaskError(w, id, err)
			return
		}
		var result any
		if err := json.Unmarshal([]byte(*t.Result), &result); err != nil {
			result = *t.Result
		}
		writeToolResult(w, id, map[string]any{"task_id": t.ID, "status": t.Status, "ready": true, "result": result}, fmt.Sprintf("Task %s completed and its result is ready.", t.ID))

	case "cancel_task":
		args, ok := decodeTaskIDArgs(p.Arguments)
		if !ok {
			writeToolError(w, id, "task_id is required")
			return
		}
		if err := h.tasks.Cancel(r.Context(), args.TaskID); err != nil {
			writeTaskError(w, id, err)
			return
		}
		writeToolResult(w, id, map[string]any{"task_id": args.TaskID, "status": "canceled"}, fmt.Sprintf("Task %s was canceled.", args.TaskID))

	default:
		writeToolError(w, id, "unknown tool: "+p.Name)
	}
}

func toolDefinitions() []map[string]any {
	return []map[string]any{
		{
			"name": "submit_task", "title": "Submit task",
			"description": "Queue a new asynchronous task in Personal Agent Runtime. Returns a durable task_id immediately; use get_task to inspect progress later.",
			"inputSchema": objectSchema(map[string]any{"prompt": map[string]any{"type": "string", "minLength": 1, "maxLength": 16384, "description": "The goal or work request to queue."}}, []string{"prompt"}),
			"annotations": map[string]any{"readOnlyHint": false, "destructiveHint": false, "idempotentHint": false, "openWorldHint": false},
		},
		{
			"name": "get_task", "title": "Get task status",
			"description": "Read the persisted status, stage, progress, and metadata for a task by task_id.",
			"inputSchema": taskIDSchema(),
			"annotations": map[string]any{"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false},
		},
		{
			"name": "get_task_result", "title": "Get task result",
			"description": "Read the final result for a completed task. If it is not complete yet, returns ready=false and the current status.",
			"inputSchema": taskIDSchema(),
			"annotations": map[string]any{"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false},
		},
		{
			"name": "cancel_task", "title": "Cancel task",
			"description": "Cancel a queued or running task. Use only when the user explicitly wants that task stopped.",
			"inputSchema": taskIDSchema(),
			"annotations": map[string]any{"readOnlyHint": false, "destructiveHint": true, "idempotentHint": false, "openWorldHint": false},
		},
	}
}

func taskIDSchema() map[string]any {
	return objectSchema(map[string]any{"task_id": map[string]any{"type": "string", "minLength": 1, "description": "Durable task identifier returned by submit_task."}}, []string{"task_id"})
}

func objectSchema(properties map[string]any, required []string) map[string]any {
	return map[string]any{"type": "object", "properties": properties, "required": required, "additionalProperties": false}
}

func decodeTaskIDArgs(raw json.RawMessage) (taskIDArgs, bool) {
	var args taskIDArgs
	if err := json.Unmarshal(raw, &args); err != nil || strings.TrimSpace(args.TaskID) == "" {
		return taskIDArgs{}, false
	}
	args.TaskID = strings.TrimSpace(args.TaskID)
	return args, true
}

func negotiateProtocolVersion(requested string) string {
	switch requested {
	case "2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26":
		return requested
	default:
		return "2025-06-18"
	}
}

func decodeID(raw json.RawMessage) any {
	if len(raw) == 0 || string(raw) == "null" {
		return nil
	}
	var v any
	if err := json.Unmarshal(raw, &v); err != nil {
		return nil
	}
	return v
}

func ensureEOF(dec *json.Decoder) error {
	var extra any
	err := dec.Decode(&extra)
	if errors.Is(err, io.EOF) {
		return nil
	}
	if err == nil {
		return errors.New("multiple JSON values")
	}
	return err
}

func writeTaskError(w http.ResponseWriter, id any, err error) {
	switch {
	case errors.Is(err, task.ErrNotFound):
		writeToolError(w, id, "task not found")
	case errors.Is(err, task.ErrTerminal):
		writeToolError(w, id, "task is already terminal")
	default:
		writeToolError(w, id, err.Error())
	}
}

func writeToolResult(w http.ResponseWriter, id any, structured any, text string) {
	writeRPCResult(w, id, map[string]any{
		"structuredContent": structured,
		"content": []map[string]any{{"type": "text", "text": text}},
		"isError": false,
	})
}

func writeToolError(w http.ResponseWriter, id any, message string) {
	writeRPCResult(w, id, map[string]any{
		"content": []map[string]any{{"type": "text", "text": message}},
		"isError": true,
	})
}

func writeRPCResult(w http.ResponseWriter, id any, result any) {
	writeHTTPJSON(w, http.StatusOK, rpcResponse{JSONRPC: "2.0", ID: id, Result: result})
}

func writeRPCError(w http.ResponseWriter, id any, code int, message string) {
	writeHTTPJSON(w, http.StatusOK, rpcResponse{JSONRPC: "2.0", ID: id, Error: &rpcError{Code: code, Message: message}})
}

func writeHTTPJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
