package document

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/ledongthuc/pdf"
	pdfcpuapi "github.com/pdfcpu/pdfcpu/pkg/api"
	"github.com/pdfcpu/pdfcpu/pkg/pdfcpu/model"
)

const (
	MaxPDFBytes          int64 = 25 * 1024 * 1024
	MaxTextBytes         int64 = 2 * 1024 * 1024
	MaxDocumentListItems       = 200
)

var (
	ErrInvalidPath  = errors.New("document path must be relative to the document root")
	ErrPDFTooLarge  = errors.New("PDF exceeds the maximum size")
	ErrTextTooLarge = errors.New("extracted PDF text exceeds the maximum size")
)

type Service struct {
	root *os.Root
}

type PDFResult struct {
	Path      string `json:"path"`
	Bytes     int64  `json:"bytes"`
	Pages     int    `json:"pages"`
	SHA256    string `json:"sha256"`
	Text      string `json:"text"`
	Encrypted bool   `json:"encrypted"`
}

type DocumentEntry struct {
	Path    string `json:"path"`
	Bytes   int64  `json:"bytes"`
	Updated string `json:"updated"`
}

func NewService(root string) (*Service, error) {
	root = strings.TrimSpace(root)
	if root == "" {
		return nil, errors.New("document root is required")
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("resolve document root: %w", err)
	}
	if err := os.MkdirAll(abs, 0o700); err != nil {
		return nil, fmt.Errorf("create document root: %w", err)
	}
	handle, err := os.OpenRoot(abs)
	if err != nil {
		return nil, fmt.Errorf("open document root: %w", err)
	}
	return &Service{root: handle}, nil
}

func (s *Service) Close() error {
	if s == nil || s.root == nil {
		return nil
	}
	return s.root.Close()
}

func (s *Service) ExtractPDF(ctx context.Context, relativePath, password string) (PDFResult, error) {
	clean, err := cleanRelativePath(relativePath)
	if err != nil {
		return PDFResult{}, err
	}
	if err := ctx.Err(); err != nil {
		return PDFResult{}, err
	}

	f, err := s.root.Open(clean)
	if err != nil {
		return PDFResult{}, fmt.Errorf("open document: %w", err)
	}
	defer f.Close()
	info, err := f.Stat()
	if err != nil {
		return PDFResult{}, fmt.Errorf("stat document: %w", err)
	}
	if !info.Mode().IsRegular() {
		return PDFResult{}, errors.New("document must be a regular file")
	}
	if info.Size() > MaxPDFBytes {
		return PDFResult{}, ErrPDFTooLarge
	}

	raw, err := io.ReadAll(io.LimitReader(f, MaxPDFBytes+1))
	if err != nil {
		return PDFResult{}, fmt.Errorf("read document: %w", err)
	}
	if int64(len(raw)) > MaxPDFBytes {
		return PDFResult{}, ErrPDFTooLarge
	}
	digest := sha256.Sum256(raw)

	reader, encrypted, err := openPDF(raw, password)
	password = ""
	if err != nil {
		return PDFResult{}, fmt.Errorf("open encrypted PDF: %w", err)
	}
	if err := ctx.Err(); err != nil {
		return PDFResult{}, err
	}
	plain, err := reader.GetPlainText()
	if err != nil {
		return PDFResult{}, fmt.Errorf("extract PDF text: %w", err)
	}
	text, err := io.ReadAll(io.LimitReader(plain, MaxTextBytes+1))
	if err != nil {
		return PDFResult{}, fmt.Errorf("read extracted PDF text: %w", err)
	}
	if int64(len(text)) > MaxTextBytes {
		return PDFResult{}, ErrTextTooLarge
	}

	return PDFResult{
		Path: clean, Bytes: info.Size(), Pages: reader.NumPage(),
		SHA256: hex.EncodeToString(digest[:]), Text: string(text), Encrypted: encrypted,
	}, nil
}

func (s *Service) ListPDFs(ctx context.Context, prefix string) ([]DocumentEntry, error) {
	prefix = strings.TrimSpace(prefix)
	if prefix == "" {
		prefix = "."
	} else {
		clean, err := cleanRelativePath(prefix)
		if err != nil {
			return nil, err
		}
		prefix = clean
	}
	entries := make([]DocumentEntry, 0)
	if err := s.listPDFs(ctx, prefix, &entries); err != nil {
		return nil, err
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Path < entries[j].Path })
	return entries, nil
}

func (s *Service) listPDFs(ctx context.Context, dirPath string, entries *[]DocumentEntry) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	dir, err := s.root.Open(dirPath)
	if err != nil {
		return fmt.Errorf("list document directory: %w", err)
	}
	defer dir.Close()
	children, err := dir.Readdir(-1)
	if err != nil {
		return fmt.Errorf("read document directory: %w", err)
	}
	for _, child := range children {
		if err := ctx.Err(); err != nil {
			return err
		}
		name := child.Name()
		path := name
		if dirPath != "." {
			path = filepath.Join(dirPath, name)
		}
		if child.IsDir() {
			if err := s.listPDFs(ctx, path, entries); err != nil {
				return err
			}
			continue
		}
		if !strings.EqualFold(filepath.Ext(name), ".pdf") {
			continue
		}
		*entries = append(*entries, DocumentEntry{
			Path: filepath.ToSlash(path), Bytes: child.Size(), Updated: child.ModTime().UTC().Format("2006-01-02T15:04:05.999999999Z07:00"),
		})
		if len(*entries) > MaxDocumentListItems {
			return errors.New("document inbox contains too many PDF files")
		}
	}
	return nil
}

func openPDF(raw []byte, password string) (*pdf.Reader, bool, error) {
	if looksEncrypted(raw) {
		if strings.TrimSpace(password) == "" {
			return nil, false, errors.New("encrypted PDF requires a password")
		}
		return decryptPDFReader(raw, password)
	}

	reader, directErr := pdf.NewReaderEncrypted(bytes.NewReader(raw), int64(len(raw)), func() string {
		return password
	})
	if directErr == nil {
		return reader, false, nil
	}
	if strings.TrimSpace(password) == "" {
		return nil, false, directErr
	}
	return decryptPDFReader(raw, password)
}

func decryptPDFReader(raw []byte, password string) (*pdf.Reader, bool, error) {
	conf := model.NewDefaultConfiguration()
	conf.UserPW = password
	conf.OwnerPW = password
	var decrypted bytes.Buffer
	if err := decryptPDF(bytes.NewReader(raw), &decrypted, conf); err != nil {
		return nil, false, err
	}
	reader, err := pdf.NewReader(bytes.NewReader(decrypted.Bytes()), int64(decrypted.Len()))
	if err != nil {
		return nil, false, err
	}
	return reader, true, nil
}

func looksEncrypted(raw []byte) bool {
	return bytes.Contains(raw, []byte("/Encrypt")) || bytes.Contains(raw, []byte("/Filter/Standard"))
}

func decryptPDF(input io.ReadSeeker, output io.Writer, conf *model.Configuration) (err error) {
	defer func() {
		if recovered := recover(); recovered != nil {
			err = fmt.Errorf("PDF decryptor rejected input: %v", recovered)
		}
	}()
	return pdfcpuapi.Decrypt(input, output, conf)
}

func cleanRelativePath(path string) (string, error) {
	path = strings.TrimSpace(path)
	if path == "" || filepath.IsAbs(path) {
		return "", ErrInvalidPath
	}
	clean := filepath.Clean(filepath.FromSlash(path))
	if clean == "." || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", ErrInvalidPath
	}
	return clean, nil
}
