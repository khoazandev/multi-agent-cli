package profile

import (
	"archive/tar"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

var ignoredDirNames = map[string]bool{
	".cache":             true,
	".npm":               true,
	".yarn":              true,
	".pnpm-store":        true,
	".rustup":            true,
	".cargo":             true,
	".docker":            true,
	".Trash":             true,
	"node_modules":       true,
	"colima":             true,
	"_lima":              true,
	"_disks":             true,
	"Caches":             true,
	"GPUCache":           true,
	"DawnGraphiteCache":  true,
	"DawnWebGPUCache":    true,
	"Code Cache":         true,
	"CachedData":         true,
	"CachedProfilesData": true,
	"Crashpad":           true,
	"crashes":            true,
}

// ShouldSkipArchiveEntry determines whether a file or directory inside a profile should be excluded from export/cloning.
func ShouldSkipArchiveEntry(relPathInsideProfile string, info os.FileInfo) (skip bool, isDir bool) {
	if relPathInsideProfile == "." || relPathInsideProfile == "" {
		return false, false
	}

	if info == nil {
		return false, false
	}

	// Skip special non-regular files (sockets, fifos, devices)
	if info.Mode()&(os.ModeSocket|os.ModeNamedPipe|os.ModeDevice) != 0 {
		return true, info.IsDir()
	}

	base := info.Name()
	if base == ".agys.lock" || base == ".keychain.lock" || base == ".DS_Store" {
		return true, info.IsDir()
	}
	// Skip host symlinked .gitconfig to avoid baking host-specific paths into export archive
	if base == ".gitconfig" && (info.Mode()&os.ModeSymlink != 0) {
		return true, false
	}

	parts := strings.Split(filepath.ToSlash(relPathInsideProfile), "/")
	for i, part := range parts {
		if ignoredDirNames[part] {
			return true, info.IsDir()
		}
		// Profile root specific skips
		if i == 0 && (part == "go" || part == ".npm" || part == ".cache" || part == ".rustup" || part == ".cargo" || part == ".docker") {
			return true, info.IsDir()
		}
	}

	// ide-data temporary logs or blob storage
	if strings.HasPrefix(relPathInsideProfile, "ide-data/logs") ||
		strings.HasPrefix(relPathInsideProfile, "ide-data/blob_storage") {
		return true, info.IsDir()
	}

	return false, false
}

// ExportProfile packages a profile directory into a gzipped tar archive.
func ExportProfile(profileName string, writer io.Writer) error {
	exists, profileDir, err := Exists(profileName)
	if err != nil {
		return err
	}
	if !exists {
		return fmt.Errorf("profile %q does not exist", profileName)
	}

	gw := gzip.NewWriter(writer)
	defer gw.Close()

	tw := tar.NewWriter(gw)
	defer tw.Close()

	baseDir := filepath.Dir(profileDir)
	err = filepath.Walk(profileDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}

		relInsideProfile, err := filepath.Rel(profileDir, path)
		if err != nil {
			return err
		}

		skip, isDir := ShouldSkipArchiveEntry(relInsideProfile, info)
		if skip {
			if isDir {
				return filepath.SkipDir
			}
			return nil
		}

		relPath, err := filepath.Rel(baseDir, path)
		if err != nil {
			return err
		}

		relPath = filepath.ToSlash(relPath)

		header, err := tar.FileInfoHeader(info, info.Name())
		if err != nil {
			return err
		}

		header.Name = relPath

		if info.Mode()&os.ModeSymlink != 0 {
			linkTarget, err := os.Readlink(path)
			if err != nil {
				return err
			}
			header.Linkname = linkTarget
		}

		if err := tw.WriteHeader(header); err != nil {
			return err
		}

		if info.Mode().IsRegular() {
			file, err := os.Open(path)
			if err != nil {
				return err
			}
			_, err = io.Copy(tw, file)
			file.Close()
			if err != nil {
				return err
			}
		}

		return nil
	})
	if err != nil {
		return err
	}

	if err := tw.Close(); err != nil {
		return err
	}
	return gw.Close()
}

// ExportAll packages all existing profiles into a single gzipped tar archive.
func ExportAll(writer io.Writer) error {
	profiles, err := List()
	if err != nil {
		return err
	}
	if len(profiles) == 0 {
		return fmt.Errorf("no profiles found to export")
	}

	gw := gzip.NewWriter(writer)
	defer gw.Close()

	tw := tar.NewWriter(gw)
	defer tw.Close()

	baseDir, err := GetBaseDir()
	if err != nil {
		return err
	}

	for _, pName := range profiles {
		profileDir := filepath.Join(baseDir, pName)
		err = filepath.Walk(profileDir, func(path string, info os.FileInfo, err error) error {
			if err != nil {
				return err
			}

			relInsideProfile, err := filepath.Rel(profileDir, path)
			if err != nil {
				return err
			}

			skip, isDir := ShouldSkipArchiveEntry(relInsideProfile, info)
			if skip {
				if isDir {
					return filepath.SkipDir
				}
				return nil
			}

			relPath, err := filepath.Rel(baseDir, path)
			if err != nil {
				return err
			}

			relPath = filepath.ToSlash(relPath)

			header, err := tar.FileInfoHeader(info, info.Name())
			if err != nil {
				return err
			}

			header.Name = relPath

			if info.Mode()&os.ModeSymlink != 0 {
				linkTarget, err := os.Readlink(path)
				if err != nil {
					return err
				}
				header.Linkname = linkTarget
			}

			if err := tw.WriteHeader(header); err != nil {
				return err
			}

			if info.Mode().IsRegular() {
				file, err := os.Open(path)
				if err != nil {
					return err
				}
				_, err = io.Copy(tw, file)
				file.Close()
				if err != nil {
					return err
				}
			}

			return nil
		})
		if err != nil {
			return err
		}
	}

	if err := tw.Close(); err != nil {
		return err
	}
	return gw.Close()
}
