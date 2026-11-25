package cmd

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/quaywin/agys/pkg/profile"
)

func TestFailedExportKeepsPreviousArchive(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "export.tgz")
	if err := os.WriteFile(target, []byte("previous archive"), 0600); err != nil {
		t.Fatal(err)
	}
	failure := errors.New("simulated archive writer failure")
	err := writeProfileArchive(target, func(writer io.Writer) error {
		if _, err := io.WriteString(writer, "partial archive"); err != nil {
			return err
		}
		return failure
	})
	if !errors.Is(err, failure) {
		t.Fatalf("writeProfileArchive returned %v; want %v", err, failure)
	}
	if content, err := os.ReadFile(target); err != nil || string(content) != "previous archive" {
		t.Errorf("previous archive = %q, %v", content, err)
	}
	if files, err := os.ReadDir(dir); err != nil || len(files) != 1 {
		t.Errorf("temporary file cleanup left %v, failure %v", files, err)
	}
}

func TestExportReplacesArchiveWithPrivatePermissions(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix permission bits unavailable")
	}
	dir := t.TempDir()
	t.Setenv("AGYS_DIR", filepath.Join(dir, "agys"))
	profileDir, err := profile.GetProfileDir("sample")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(profileDir, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(profileDir, "data"), []byte("sample profile"), 0600); err != nil {
		t.Fatal(err)
	}
	target := filepath.Join(dir, "export.tgz")
	if err := os.WriteFile(target, []byte("previous archive"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(target, 0644); err != nil {
		t.Fatal(err)
	}
	originalOutput, originalAll := outputFile, exportAll
	t.Cleanup(func() { outputFile, exportAll = originalOutput, originalAll })
	outputFile, exportAll = target, false

	if err := exportCmd.RunE(exportCmd, []string{"sample"}); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(target)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got != 0600 {
		t.Errorf("archive permissions = %04o, want 0600", got)
	}
	archive, err := os.Open(target)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	if err := profile.ImportProfile(archive, "restored", false); err != nil {
		t.Fatalf("exported archive does not restore: %v", err)
	}
}
