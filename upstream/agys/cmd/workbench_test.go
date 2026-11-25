package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/quaywin/agys/pkg/profile"
)

func TestWorkbenchResumeIsolation(t *testing.T) {
	home := t.TempDir()
	t.Setenv("MULTI_AGENT_CLI", "1")
	t.Setenv("HOME", home)
	t.Setenv("AGYS_REAL_HOME", home)
	t.Setenv("AGYS_DIR", filepath.Join(home, ".agys"))

	work := filepath.Join(home, "work", "repo")
	other := filepath.Join(home, "other", "repo")
	for _, repo := range []string{work, other} {
		if err := os.MkdirAll(filepath.Join(repo, ".git"), 0700); err != nil {
			t.Fatal(err)
		}
	}

	writeConversation := func(name, id, repo string) string {
		t.Helper()
		profileDir, err := profile.GetProfileDir(name)
		if err != nil {
			t.Fatal(err)
		}
		cliDir := filepath.Join(profileDir, ".gemini", "antigravity-cli")
		brain := filepath.Join(cliDir, "brain", id)
		if err := os.MkdirAll(brain, 0700); err != nil {
			t.Fatal(err)
		}
		entry, err := json.Marshal(map[string]string{"conversationId": id, "workspace": repo})
		if err != nil {
			t.Fatal(err)
		}
		history, err := os.OpenFile(filepath.Join(cliDir, "history.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := history.Write(append(entry, '\n')); err != nil {
			t.Fatal(err)
		}
		if err := history.Close(); err != nil {
			t.Fatal(err)
		}
		return brain
	}

	foreignProfileBrain := writeConversation("alpha", "foreign-profile", work)
	writeConversation("beta", "foreign-repo", other)
	if err := profile.SaveLastConversation("foreign-profile"); err != nil {
		t.Fatal(err)
	}

	for _, args := range [][]string{
		{"--continue"},
		{"--conversation=foreign-profile"},
		{"--resume", "foreign-repo"},
		{"-r", "foreign-repo"},
		{"-conversation=foreign-repo"},
		{"--conversation=missing"},
	} {
		if _, _, err := resolveResumeProfile("beta", args, work); err == nil {
			t.Errorf("resolveResumeProfile with %v accepted a session outside account beta and repo %s", args, work)
		}
	}
	if _, err := os.Stat(foreignProfileBrain); err != nil {
		t.Fatalf("foreign profile brain was modified: %v", err)
	}

	writeConversation("beta", "owned", work)
	for _, args := range [][]string{
		{"--continue"},
		{"--resume", "--model", "gemini"},
		{"--conversation=owned"},
		{"-r=owned"},
		{"-c=true"},
	} {
		name, resolved, err := resolveResumeProfile("beta", args, work)
		if err != nil || name != "beta" || len(resolved) == 0 || resolved[0] != "--conversation=owned" {
			t.Errorf("resolveResumeProfile with %v = %q, %v, %v; want beta and owned session", args, name, resolved, err)
		}
	}

	args := []string{"-p", "new task"}
	if _, resolved, err := resolveResumeProfile("beta", args, work); err != nil || !reflect.DeepEqual(resolved, args) {
		t.Errorf("fresh session = %v, %v; want %v", resolved, err, args)
	}
	for _, args := range [][]string{
		{"--project=other", "--continue"},
		{"--continue", "--conversation", "foreign-repo"},
	} {
		if _, _, err := resolveResumeProfile("beta", args, work); err == nil {
			t.Errorf("resolveResumeProfile with conflicting arguments %v succeeded", args)
		}
	}
}
