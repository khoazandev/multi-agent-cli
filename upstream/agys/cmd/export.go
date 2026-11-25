package cmd

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/quaywin/agys/pkg/profile"
	"github.com/spf13/cobra"
)

var (
	exportAll  bool
	outputFile string
)

var exportCmd = &cobra.Command{
	Use:               "export [profile_name]",
	Short:             "Export a profile (or all profiles) to a gzipped tar archive",
	Long:              `Packages all profile configuration and credentials into a compressed .tar.gz archive. Use the --all flag to export all profiles.`,
	ValidArgsFunction: CompleteProfileNames,
	Args:              cobra.MaximumNArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		if exportAll {
			if len(args) > 0 {
				return fmt.Errorf("cannot specify a profile name when exporting all profiles (--all)")
			}

			outPath := outputFile
			if outPath == "" {
				outPath = "agys_profiles_backup.tar.gz"
			}

			fmt.Println("Exporting all profiles...")
			if err := writeProfileArchive(outPath, profile.ExportAll); err != nil {
				return err
			}

			absPath, err := filepath.Abs(outPath)
			if err != nil {
				absPath = outPath
			}
			fmt.Printf("Successfully exported all profiles to %s\n", absPath)
			return nil
		}

		if len(args) == 0 {
			return fmt.Errorf("must specify a profile name to export, or use --all")
		}

		profileName := args[0]

		exists, _, err := profile.Exists(profileName)
		if err != nil {
			return err
		}
		if !exists {
			return fmt.Errorf("profile %q does not exist", profileName)
		}

		outPath := outputFile
		if outPath == "" {
			outPath = profileName + ".tar.gz"
		}

		fmt.Printf("Exporting profile %q...\n", profileName)
		if err := writeProfileArchive(outPath, func(writer io.Writer) error {
			return profile.ExportProfile(profileName, writer)
		}); err != nil {
			return err
		}

		absPath, err := filepath.Abs(outPath)
		if err != nil {
			absPath = outPath
		}
		fmt.Printf("Successfully exported profile %q to %s\n", profileName, absPath)
		return nil
	},
}

func writeProfileArchive(path string, export func(io.Writer) error) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return fmt.Errorf("create export directory: %w", err)
	}
	file, err := os.CreateTemp(dir, ".agys-export-*")
	if err != nil {
		return fmt.Errorf("create temporary archive: %w", err)
	}
	temporary := file.Name()
	defer os.Remove(temporary)

	writeErr := export(file)
	if writeErr == nil {
		writeErr = file.Sync()
	}
	if err := errors.Join(writeErr, file.Close()); err != nil {
		return fmt.Errorf("write profile archive: %w", err)
	}
	if err := os.Rename(temporary, path); err != nil {
		return fmt.Errorf("publish profile archive: %w", err)
	}
	return nil
}

func init() {
	exportCmd.Flags().BoolVarP(&exportAll, "all", "a", false, "Export all profiles to a single archive")
	exportCmd.Flags().StringVarP(&outputFile, "out", "o", "", "Output file path for the exported archive")

	_ = exportCmd.RegisterFlagCompletionFunc("out", cobra.FixedCompletions(nil, cobra.ShellCompDirectiveDefault))

	rootCmd.AddCommand(exportCmd)
}
