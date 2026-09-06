package artifact

import (
	"bytes"
	"context"
	"path/filepath"
	"testing"
)

func TestStorePutAndRead(t *testing.T) {
	store, err := NewStore(filepath.Join(t.TempDir(), "artifacts"))
	if err != nil {
		t.Fatalf("new store: %v", err)
	}
	want := []byte("artifact payload")
	ref, err := store.Put(context.Background(), "tsk_123", "result.json", want)
	if err != nil {
		t.Fatalf("put: %v", err)
	}
	if ref != "tsk_123/result.json" {
		t.Fatalf("ref=%q", ref)
	}
	got, err := store.Read(context.Background(), ref)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("got=%q want=%q", got, want)
	}
}

func TestStoreRejectsTraversal(t *testing.T) {
	store, err := NewStore(filepath.Join(t.TempDir(), "artifacts"))
	if err != nil {
		t.Fatalf("new store: %v", err)
	}
	if _, err := store.Put(context.Background(), "../escape", "result.json", []byte("x")); err == nil {
		t.Fatal("expected invalid task id error")
	}
	if _, err := store.Put(context.Background(), "tsk_123", "../escape", []byte("x")); err == nil {
		t.Fatal("expected invalid artifact name error")
	}
	if _, err := store.Put(context.Background(), ".", "result.json", []byte("x")); err == nil {
		t.Fatal("expected dot task id rejection")
	}
	if _, err := store.Put(context.Background(), "..", "result.json", []byte("x")); err == nil {
		t.Fatal("expected dot-dot task id rejection")
	}
	if _, err := store.Put(context.Background(), "tsk_123", ".", []byte("x")); err == nil {
		t.Fatal("expected dot artifact name rejection")
	}
	if _, err := store.Put(context.Background(), "tsk_123", "..", []byte("x")); err == nil {
		t.Fatal("expected dot-dot artifact name rejection")
	}
	if _, err := store.Read(context.Background(), "../escape"); err == nil {
		t.Fatal("expected traversal read rejection")
	}
}

func TestStoreHonorsCanceledContext(t *testing.T) {
	store, err := NewStore(filepath.Join(t.TempDir(), "artifacts"))
	if err != nil {
		t.Fatalf("new store: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := store.Put(ctx, "tsk_123", "result.json", []byte("x")); err == nil {
		t.Fatal("expected canceled context error")
	}
}
