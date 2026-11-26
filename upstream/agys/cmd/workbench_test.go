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

func TestDisplayProfileNameAndTokenCleanup(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("AGYS_REAL_HOME", home)
	t.Setenv("AGYS_DIR", filepath.Join(home, ".agys"))
	t.Setenv("MULTI_AGENT_CLI", "1")
	t.Setenv("MULTI_AGENT_CLI_ACCOUNT", "acc-a")

	if got := profile.DisplayProfileName("mac-1234"); got != "acc-a" {
		t.Fatalf("DisplayProfileName = %q, want acc-a", got)
	}
	t.Setenv("MULTI_AGENT_CLI_ACCOUNT", "invalid/name")
	if got := profile.DisplayProfileName("mac-1234"); got != "mac-1234" {
		t.Fatalf("DisplayProfileName with invalid account = %q, want mac-1234", got)
	}

	profileDir, err := profile.GetProfileDir("acc-a")
	if err != nil {
		t.Fatal(err)
	}
	if err := profile.WriteTokenToProfile(profileDir, `{"token":{"refresh_token":"tok"}}`); err != nil {
		t.Fatal(err)
	}
	if !profile.HasProfileToken("acc-a") {
		t.Fatalf("expected token files to be written")
	}
	profile.ClearAllTokenLocations(profileDir)
	if profile.HasProfileToken("acc-a") {
		t.Fatalf("expected ClearAllTokenLocations to remove all token copies")
	}
}

func TestWorkbenchAlwaysAcceptAndTrustedWorkspaces(t *testing.T) {
	t.Setenv("MULTI_AGENT_CLI", "1")
	t.Setenv("MULTI_AGENT_CLI_ASK_PERMISSIONS", "")

	if got := EnsureWorkbenchPermissions(nil); !reflect.DeepEqual(got, []string{"--dangerously-skip-permissions"}) {
		t.Fatalf("EnsureWorkbenchPermissions(nil) = %v", got)
	}
	if got := EnsureWorkbenchPermissions([]string{"--conversation=owned"}); !reflect.DeepEqual(got, []string{"--conversation=owned", "--dangerously-skip-permissions"}) {
		t.Fatalf("EnsureWorkbenchPermissions(conversation) = %v", got)
	}
	if got := EnsureWorkbenchPermissions([]string{"--", "fix", "bug"}); !reflect.DeepEqual(got, []string{"--dangerously-skip-permissions", "--", "fix", "bug"}) {
		t.Fatalf("EnsureWorkbenchPermissions(prompt) = %v", got)
	}

	unchangedCases := [][]string{
		{"models"},
		{"--version"},
		{"--sandbox"},
		{"--dangerously-skip-permissions", "--model", "gemini-3.8-flash"},
	}
	for _, tc := range unchangedCases {
		if got := EnsureWorkbenchPermissions(tc); !reflect.DeepEqual(got, tc) {
			t.Fatalf("EnsureWorkbenchPermissions(%v) = %v, want unchanged", tc, got)
		}
	}

	t.Setenv("MULTI_AGENT_CLI_ASK_PERMISSIONS", "1")
	if got := EnsureWorkbenchPermissions([]string{"--conversation=owned"}); !reflect.DeepEqual(got, []string{"--conversation=owned"}) {
		t.Fatalf("EnsureWorkbenchPermissions with ask override = %v", got)
	}
	t.Setenv("MULTI_AGENT_CLI_ASK_PERMISSIONS", "")
	t.Setenv("MULTI_AGENT_CLI", "")
	if got := EnsureWorkbenchPermissions([]string{"--conversation=owned"}); !reflect.DeepEqual(got, []string{"--conversation=owned"}) {
		t.Fatalf("EnsureWorkbenchPermissions outside workbench = %v", got)
	}

	home := t.TempDir()
	t.Setenv("MULTI_AGENT_CLI", "1")
	t.Setenv("HOME", home)
	t.Setenv("AGYS_REAL_HOME", home)
	t.Setenv("AGYS_DIR", filepath.Join(home, ".agys"))

	repoRoot := filepath.Join(home, "work", "repo")
	repoSubdir := filepath.Join(repoRoot, "sub", "pkg")
	sharedA := filepath.Join(home, "shared-a")
	sharedB := filepath.Join(home, "shared-b")
	for _, dir := range []string{filepath.Join(repoRoot, ".git"), repoSubdir, sharedA, sharedB} {
		if err := os.MkdirAll(dir, 0700); err != nil {
			t.Fatal(err)
		}
	}

	profileDir, err := profile.Create("acc-trust")
	if err != nil {
		t.Fatal(err)
	}
	otherProfileDir, err := profile.Create("acc-other")
	if err != nil {
		t.Fatal(err)
	}
	otherSettings := filepath.Join(otherProfileDir, ".gemini", "antigravity-cli", "settings.json")

	settingsPath := filepath.Join(profileDir, ".gemini", "antigravity-cli", "settings.json")
	if err := os.MkdirAll(filepath.Dir(settingsPath), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(settingsPath, []byte("{\"customKey\": \"keep\"}\n"), 0600); err != nil {
		t.Fatal(err)
	}

	if err := profile.EnsureWorkbenchTrustedWorkspaces(profileDir, repoSubdir, []string{"--add-dir", sharedA, "--add-dir=" + sharedB}); err != nil {
		t.Fatalf("EnsureWorkbenchTrustedWorkspaces failed: %v", err)
	}

	data, err := os.ReadFile(settingsPath)
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	if err := json.Unmarshal(data, &parsed); err != nil {
		t.Fatal(err)
	}
	if parsed["customKey"] != "keep" {
		t.Fatalf("expected existing settings key preserved, got %#v", parsed["customKey"])
	}
	rawTrusted, ok := parsed["trustedWorkspaces"].([]any)
	if !ok {
		t.Fatalf("expected trustedWorkspaces slice, got %T", parsed["trustedWorkspaces"])
	}
	trustedSet := make(map[string]bool)
	for _, item := range rawTrusted {
		if s, isStr := item.(string); isStr {
			trustedSet[s] = true
		}
	}
	for _, want := range []string{repoRoot, repoSubdir, sharedA, sharedB} {
		if !trustedSet[want] {
			t.Fatalf("expected %q in trustedWorkspaces, got %v", want, rawTrusted)
		}
	}
	if _, err := os.Stat(otherSettings); !os.IsNotExist(err) {
		t.Fatalf("expected other profile settings.json untouched, got err=%v", err)
	}
}
