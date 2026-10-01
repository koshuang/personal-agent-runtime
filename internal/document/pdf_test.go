package document

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const plainPDF = "%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\nstartxref\n0\n%%EOF\n"

func newDocumentService(t *testing.T) (*Service, string) {
	t.Helper()
	root := t.TempDir()
	service, err := NewService(root)
	if err != nil {
		t.Fatalf("new service: %v", err)
	}
	t.Cleanup(func() { _ = service.Close() })
	return service, root
}

func TestExtractPDFRejectsUnsafePaths(t *testing.T) {
	service, _ := newDocumentService(t)
	for _, path := range []string{"/tmp/statement.pdf", "../statement.pdf", ""} {
		if _, err := service.ExtractPDF(context.Background(), path, ""); err != ErrInvalidPath {
			t.Fatalf("path %q error=%v want=%v", path, err, ErrInvalidPath)
		}
	}
}

func TestExtractPDFRejectsInvalidDocumentAndDirectories(t *testing.T) {
	service, root := newDocumentService(t)
	if err := os.WriteFile(filepath.Join(root, "statement.pdf"), []byte(plainPDF), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := service.ExtractPDF(context.Background(), "statement.pdf", ""); err == nil {
		t.Fatal("expected malformed PDF error")
	}
	if err := os.Mkdir(filepath.Join(root, "folder.pdf"), 0o700); err != nil {
		t.Fatal(err)
	}
	if _, err := service.ExtractPDF(context.Background(), "folder.pdf", ""); err == nil || !strings.Contains(err.Error(), "regular file") {
		t.Fatalf("directory error=%v", err)
	}
}

func TestListPDFsReturnsRelativeHandlesAndSkipsNonPDF(t *testing.T) {
	service, root := newDocumentService(t)
	if err := os.Mkdir(filepath.Join(root, "nested"), 0o700); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"statement.PDF", filepath.Join("nested", "receipt.pdf"), "notes.txt"} {
		if err := os.WriteFile(filepath.Join(root, path), []byte("fixture"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	files, err := service.ListPDFs(context.Background(), "")
	if err != nil {
		t.Fatalf("list PDFs: %v", err)
	}
	if len(files) != 2 || files[0].Path != "nested/receipt.pdf" || files[1].Path != "statement.PDF" {
		t.Fatalf("unexpected files: %#v", files)
	}
}

func TestLooksEncryptedDetectsPDFEncryptionDictionary(t *testing.T) {
	if !looksEncrypted([]byte("%PDF-1.4\n1 0 obj<</Filter/Standard>>endobj")) {
		t.Fatal("expected encrypted PDF marker")
	}
	if looksEncrypted([]byte(plainPDF)) {
		t.Fatal("did not expect encryption marker")
	}
}
