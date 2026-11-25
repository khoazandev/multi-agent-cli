package profile

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// MigrateConversation moves a conversation's brain and database to another profile.
func MigrateConversation(convID, srcProfile, destProfile string) error {
	if !validProfileNameRegex.MatchString(convID) {
		return fmt.Errorf("invalid conversation ID %q", convID)
	}
	if srcProfile == "" || destProfile == "" {
		return fmt.Errorf("source and destination profile names cannot be empty")
	}
	if srcProfile == destProfile {
		return nil
	}
	if IsWorkbenchMode() {
		return fmt.Errorf("workbench conversations stay with their project and profile")
	}

	source, err := GetProfileDir(srcProfile)
	if err != nil {
		return err
	}
	destination, err := GetProfileDir(destProfile)
	if err != nil {
		return err
	}
	err = WithFileLock(context.Background(), func() error {
		if err := moveConversationFiles(convID, source, destination); err != nil {
			return err
		}
		migrateConversationSummary(convID, source, destination)
		migrateHistoryEntry(convID, source, destination)
		_ = SaveLastConversation(convID)
		return nil
	})
	if err != nil {
		return err
	}
	_ = UpdateSessionCache(func(cache SessionCache) error {
		delete(cache, srcProfile+":"+convID)
		return nil
	})
	return nil
}

func moveConversationFiles(id, source, destination string) error {
	type move struct{ from, to string }
	var moves []move
	foundConversation := false
	for _, app := range []string{"antigravity-cli", "antigravity"} {
		base := filepath.Join(".gemini", app)
		for _, profile := range []string{source, destination} {
			presence := filepath.Join(profile, base, "presence", id+".lock")
			if _, err := os.Lstat(presence); !errors.Is(err, os.ErrNotExist) {
				return fmt.Errorf("cannot migrate conversation %q while its presence lock exists or is unreadable at %s", id, presence)
			}
		}
		for _, path := range []string{
			filepath.Join("brain", id),
			filepath.Join("conversations", id+".db"),
			filepath.Join("conversations", id+".db-wal"),
			filepath.Join("conversations", id+".db-shm"),
			filepath.Join("annotations", id+".pbtxt"),
		} {
			from := filepath.Join(source, base, path)
			to := filepath.Join(destination, base, path)
			if _, err := os.Lstat(to); err == nil {
				return fmt.Errorf("conversation %q already exists at %s", id, to)
			} else if !errors.Is(err, os.ErrNotExist) {
				return err
			}
			info, err := os.Lstat(from)
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			if err != nil {
				return err
			}
			if info.Mode()&os.ModeSymlink != 0 {
				return fmt.Errorf("conversation path is a symlink: %s", from)
			}
			if path == filepath.Join("brain", id) || path == filepath.Join("conversations", id+".db") {
				foundConversation = true
			}
			moves = append(moves, move{from, to})
		}
	}
	if !foundConversation {
		return fmt.Errorf("conversation %q not found", id)
	}
	for _, item := range moves {
		if err := os.MkdirAll(filepath.Dir(item.to), 0700); err != nil {
			return err
		}
	}
	for i, item := range moves {
		if err := os.Rename(item.from, item.to); err != nil {
			for j := i - 1; j >= 0; j-- {
				err = errors.Join(err, os.Rename(moves[j].to, moves[j].from))
			}
			return fmt.Errorf("move conversation %q: %w", id, err)
		}
	}
	return nil
}

func migrateConversationSummary(convID, srcProfileDir, destProfileDir string) {
	sqlitePath, err := exec.LookPath("sqlite3")
	if err != nil {
		return
	}

	escapedConvID := strings.ReplaceAll(convID, "'", "''")

	for _, sub := range []string{
		filepath.Join(".gemini", "antigravity-cli", "conversation_summaries.db"),
		filepath.Join(".gemini", "antigravity", "conversation_summaries.db"),
	} {
		srcDB := filepath.Join(srcProfileDir, sub)
		destDB := filepath.Join(destProfileDir, sub)

		if _, err := os.Stat(srcDB); err != nil {
			continue
		}

		_ = os.MkdirAll(filepath.Dir(destDB), 0700)

		// Check if source DB has this conversation
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		checkCmd := exec.CommandContext(ctx, sqlitePath, srcDB, fmt.Sprintf("SELECT count(*) FROM conversation_summaries WHERE conversation_id = '%s';", escapedConvID))
		out, err := checkCmd.Output()
		cancel()
		if err != nil || strings.TrimSpace(string(out)) == "0" {
			continue
		}

		// If destination DB doesn't exist yet, initialize schema from source
		if _, err := os.Stat(destDB); os.IsNotExist(err) {
			ctxSchema, cancelSchema := context.WithTimeout(context.Background(), 2*time.Second)
			dumpCmd := exec.CommandContext(ctxSchema, sqlitePath, srcDB, ".schema")
			schemaOut, schemaErr := dumpCmd.Output()
			cancelSchema()
			if schemaErr == nil && len(schemaOut) > 0 {
				ctxInit, cancelInit := context.WithTimeout(context.Background(), 2*time.Second)
				initCmd := exec.CommandContext(ctxInit, sqlitePath, destDB, string(schemaOut))
				_ = initCmd.Run()
				cancelInit()
			}
		}

		// Leave existing destination and source rows intact if the copy fails.
		escapedSrcDB := strings.ReplaceAll(srcDB, "'", "''")
		attachSQL := fmt.Sprintf(
			"ATTACH DATABASE '%s' AS src; INSERT INTO conversation_summaries SELECT * FROM src.conversation_summaries WHERE conversation_id = '%s'; DETACH DATABASE src;",
			escapedSrcDB, escapedConvID,
		)
		ctxMigrate, cancelMigrate := context.WithTimeout(context.Background(), 2*time.Second)
		migrateCmd := exec.CommandContext(ctxMigrate, sqlitePath, destDB, attachSQL)
		err = migrateCmd.Run()
		cancelMigrate()
		if err != nil {
			continue
		}

		// Delete row from source DB to keep it clean
		ctxDel, cancelDel := context.WithTimeout(context.Background(), 2*time.Second)
		delCmd := exec.CommandContext(ctxDel, sqlitePath, srcDB, fmt.Sprintf("DELETE FROM conversation_summaries WHERE conversation_id = '%s';", escapedConvID))
		_ = delCmd.Run()
		cancelDel()
	}
}

func migrateHistoryEntry(convID, srcProfileDir, destProfileDir string) {
	for _, rel := range []string{
		filepath.Join(".gemini", "antigravity-cli", "history.jsonl"),
		filepath.Join(".gemini", "antigravity", "history.jsonl"),
	} {
		srcHistory := filepath.Join(srcProfileDir, rel)
		data, err := os.ReadFile(srcHistory)
		if err != nil || len(data) == 0 {
			continue
		}

		destHistory := filepath.Join(destProfileDir, rel)
		existingData, _ := os.ReadFile(destHistory)
		existingContent := string(existingData)

		var toAppend []string
		scanner := bufio.NewScanner(strings.NewReader(string(data)))
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" || !strings.Contains(line, "conversationId") {
				continue
			}

			var item struct {
				ConversationID string `json:"conversationId"`
			}
			if err := json.Unmarshal([]byte(line), &item); err == nil && item.ConversationID == convID {
				if !strings.Contains(existingContent, line) {
					toAppend = append(toAppend, line)
				}
			}
		}

		if len(toAppend) > 0 {
			_ = os.MkdirAll(filepath.Dir(destHistory), 0700)
			f, err := os.OpenFile(destHistory, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
			if err == nil {
				for _, line := range toAppend {
					_, _ = f.WriteString(line + "\n")
				}
				_ = f.Close()
			}
		}
	}
}
