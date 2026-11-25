package profile

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// ImportProfile restores an archive under the selected profile name.
func ImportProfile(reader io.Reader, name string, overwrite bool) error {
	if err := ValidateName(name); err != nil {
		return err
	}
	return importProfiles(reader, name, overwrite)
}

// ImportAll restores the profiles named by the archive.
func ImportAll(reader io.Reader, overwrite bool) error {
	return importProfiles(reader, "", overwrite)
}

func importProfiles(reader io.Reader, targetName string, overwrite bool) error {
	gr, err := gzip.NewReader(reader)
	if err != nil {
		return fmt.Errorf("open gzip archive: %w", err)
	}
	defer gr.Close()

	base, err := GetBaseDir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(base, 0700); err != nil {
		return err
	}
	stage, err := os.MkdirTemp(filepath.Dir(base), ".profile-import-")
	if err != nil {
		return err
	}
	keepStage := false
	defer func() {
		if !keepStage {
			_ = os.RemoveAll(stage)
		}
	}()
	incoming, backup := filepath.Join(stage, "incoming"), filepath.Join(stage, "backup")
	if err := os.Mkdir(backup, 0700); err != nil {
		return err
	}

	names, err := extractProfiles(tar.NewReader(gr), incoming, targetName)
	if err != nil {
		return err
	}
	// The tar reader can finish before gzip has verified its checksum.
	if _, err := io.Copy(io.Discard, gr); err != nil {
		return fmt.Errorf("verify gzip archive: %w", err)
	}
	return WithFileLock(context.Background(), func() error {
		var err error
		keepStage, err = publishProfiles(base, incoming, backup, names, overwrite)
		return err
	})
}

func extractProfiles(tr *tar.Reader, incoming, targetName string) ([]string, error) {
	roots := make(map[string]*os.Root)
	defer func() {
		for _, root := range roots {
			_ = root.Close()
		}
	}()

	archivePrefix := ""
	for {
		header, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, fmt.Errorf("read tar archive: %w", err)
		}

		name, path, err := profileArchiveName(header, targetName, &archivePrefix)
		if err != nil {
			return nil, err
		}
		if name == "" {
			continue
		}

		root := roots[name]
		if root == nil {
			dir := filepath.Join(incoming, name)
			if err := os.MkdirAll(dir, 0700); err != nil {
				return nil, err
			}
			root, err = os.OpenRoot(dir)
			if err != nil {
				return nil, err
			}
			roots[name] = root
		}
		if path == "." {
			if header.Typeflag != tar.TypeDir {
				return nil, fmt.Errorf("profile root must be a directory: %q", header.Name)
			}
			continue
		}
		if err := extractProfileEntry(root, path, header, tr); err != nil {
			return nil, fmt.Errorf("extract %q: %w", header.Name, err)
		}
	}

	if targetName != "" && len(roots) == 0 {
		return nil, fmt.Errorf("archive contains no profile entries")
	}
	names := make([]string, 0, len(roots))
	for name := range roots {
		names = append(names, name)
	}
	sort.Strings(names)
	return names, nil
}

func profileArchiveName(header *tar.Header, targetName string, archivePrefix *string) (name, path string, err error) {
	path = filepath.FromSlash(header.Name)
	if !filepath.IsLocal(path) {
		return "", "", fmt.Errorf("tar entry attempts directory traversal: %s", header.Name)
	}
	path = filepath.Clean(path)
	if path == "." {
		return "", "", nil
	}
	head, rest, _ := strings.Cut(path, string(filepath.Separator))
	if targetName == "" {
		if err := ValidateName(head); err != nil {
			return "", "", fmt.Errorf("invalid archive profile name %q: %w", head, err)
		}
		return head, filepath.Clean(rest), nil
	}
	if *archivePrefix == "" && validProfileNameRegex.MatchString(head) && (rest != "" || header.Typeflag == tar.TypeDir) {
		*archivePrefix = head
	}
	if *archivePrefix == head {
		path = filepath.Clean(rest)
	}
	return targetName, path, nil
}

func extractProfileEntry(root *os.Root, path string, header *tar.Header, contents io.Reader) error {
	switch header.Typeflag {
	case tar.TypeDir:
		return root.MkdirAll(path, header.FileInfo().Mode().Perm())
	case tar.TypeReg, tar.TypeSymlink:
		if err := root.MkdirAll(filepath.Dir(path), 0700); err != nil {
			return err
		}
	default:
		return fmt.Errorf("unsupported archive entry type %d", header.Typeflag)
	}

	if header.Typeflag == tar.TypeSymlink {
		target := filepath.FromSlash(header.Linkname)
		resolved := filepath.Join(filepath.Dir(path), target)
		if target == "" || filepath.IsAbs(target) || filepath.VolumeName(target) != "" || !filepath.IsLocal(resolved) {
			return fmt.Errorf("symlink target escapes the profile")
		}
		return root.Symlink(target, path)
	}
	file, err := root.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, header.FileInfo().Mode().Perm())
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(file, contents)
	return errors.Join(copyErr, file.Close())
}

func publishProfiles(base, incoming, backup string, names []string, overwrite bool) (keepStage bool, err error) {
	// Validate all targets before any publication.
	existing := make(map[string]bool)
	for _, name := range names {
		target := filepath.Join(base, name)
		_, statErr := os.Lstat(target)
		if errors.Is(statErr, os.ErrNotExist) {
			continue
		}
		if statErr != nil {
			return false, statErr
		}
		if !overwrite {
			return false, fmt.Errorf("profile %q already exists; use --force to overwrite", name)
		}
		existing[name] = true
	}

	type change struct {
		name     string
		backedUp bool
		exposed  bool
	}
	var changes []change
	defer func() {
		if err == nil {
			return
		}
		for i := len(changes) - 1; i >= 0; i-- {
			edit := changes[i]
			target := filepath.Join(base, edit.name)
			if edit.exposed {
				if rollbackErr := os.Rename(target, filepath.Join(incoming, edit.name)); rollbackErr != nil {
					err = errors.Join(err, rollbackErr)
					keepStage = true
					continue
				}
			}
			if edit.backedUp {
				if rollbackErr := os.Rename(filepath.Join(backup, edit.name), target); rollbackErr != nil {
					err = errors.Join(err, rollbackErr)
					keepStage = true
				}
			}
		}
		if keepStage {
			err = fmt.Errorf("import rollback incomplete, backups retained at %s: %w", backup, err)
		}
	}()
	for _, name := range names {
		target := filepath.Join(base, name)
		edit := change{name: name}
		if existing[name] {
			if err := os.Rename(target, filepath.Join(backup, name)); err != nil {
				return false, fmt.Errorf("back up profile %q: %w", name, err)
			}
			edit.backedUp = true
		}
		changes = append(changes, edit)
		if err := os.Rename(filepath.Join(incoming, name), target); err != nil {
			return false, fmt.Errorf("restore profile %q: %w", name, err)
		}
		changes[len(changes)-1].exposed = true
	}
	return false, nil
}
