package profile

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"time"
)

// IsWorkbenchMode enables isolation rules for Multi Agent CLI.
func IsWorkbenchMode() bool {
	return os.Getenv("MULTI_AGENT_CLI") == "1"
}

var workbenchCommandName = regexp.MustCompile(`^[A-Za-z0-9_.][A-Za-z0-9_.-]*$`)

// WorkbenchResumeArgv returns a command Herdr can safely restore in its shell.
func WorkbenchResumeArgv(name, conversationID string) []string {
	runner := os.Getenv("MULTI_AGENT_CLI_RUNNER")
	if !workbenchCommandName.MatchString(runner) || ValidateName(name) != nil || !validProfileNameRegex.MatchString(conversationID) {
		return nil
	}
	return []string{runner, "run", name, "--", "--conversation=" + conversationID}
}

// WorkbenchSessions uses recorded workspace metadata from a single profile.
// Transcript text and directory names alone are insufficient evidence of ownership.
func WorkbenchSessions(name, directory string) ([]ConversationSession, error) {
	profileDir, err := GetProfileDir(name)
	if err != nil {
		return nil, err
	}
	project, err := canonicalWorkbenchRepo(directory)
	if err != nil {
		return nil, fmt.Errorf("cannot resolve current workbench project: %w", err)
	}

	var sessions []ConversationSession
	for id, history := range loadProfileHistory(profileDir) {
		if !validProfileNameRegex.MatchString(id) || IsInternalAutomatedSession(history.display) {
			continue
		}
		workspace, err := canonicalWorkbenchRepo(history.workspace)
		if err != nil || workspace != project {
			continue
		}
		if modified, exists := workbenchConversationFile(profileDir, id); exists {
			sessions = append(sessions, ConversationSession{
				Profile: name, ConvID: id, ModTime: modified,
				ProjectPath: project, ProjectName: filepath.Base(project),
				UserPrompt: cleanPromptSummary(history.display),
			})
		}
	}
	sort.Slice(sessions, func(i, j int) bool {
		if sessions[i].ModTime.Equal(sessions[j].ModTime) {
			return sessions[i].ConvID < sessions[j].ConvID
		}
		return sessions[i].ModTime.After(sessions[j].ModTime)
	})
	return sessions, nil
}

func canonicalWorkbenchRepo(directory string) (string, error) {
	if !filepath.IsAbs(directory) {
		return "", fmt.Errorf("project path must be absolute: %q", directory)
	}
	canonical, err := filepath.EvalSymlinks(directory)
	if err != nil {
		return "", err
	}
	info, err := os.Stat(canonical)
	if err != nil {
		return "", err
	}
	if !info.IsDir() {
		return "", fmt.Errorf("project path is not a directory: %q", directory)
	}
	for path := canonical; ; path = filepath.Dir(path) {
		if _, err := os.Stat(filepath.Join(path, ".git")); err == nil {
			return path, nil
		}
		if filepath.Dir(path) == path {
			return canonical, nil
		}
	}
}

func workbenchConversationFile(profileDir, id string) (time.Time, bool) {
	var modified time.Time
	for _, app := range []string{"antigravity-cli", "antigravity"} {
		dir := filepath.Join(profileDir, ".gemini", app)
		brain := filepath.Join(dir, "brain", id)
		db := filepath.Join(dir, "conversations", id+".db")
		for _, path := range []string{brain, db, filepath.Join(brain, ".system_generated", "logs", "transcript.jsonl")} {
			info, err := os.Lstat(path)
			if err == nil && info.Mode()&fs.ModeSymlink == 0 && modified.Before(info.ModTime()) {
				modified = info.ModTime()
			}
		}
	}
	return modified, !modified.IsZero()
}
