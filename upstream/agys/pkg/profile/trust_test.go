package profile

import (
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"testing"
)

func TestWorkbenchChildDoesNotInheritSharedGoogleCredentials(t *testing.T) {
	t.Setenv("MULTI_AGENT_CLI", "1")
	env := SanitizeAgyEnv([]string{
		"GEMINI_API_KEY=stub", "GOOGLE_API_KEY=stub", "GOOGLE_APPLICATION_CREDENTIALS=stub",
		"GIT_CONFIG_GLOBAL=/host/config", "UNRELATED_SERVICE_KEY=stub",
	}, map[string]string{"GIT_CONFIG_GLOBAL": "/profile/config"})
	if !slices.Contains(env, "UNRELATED_SERVICE_KEY=stub") || !slices.Contains(env, "GIT_CONFIG_GLOBAL=/profile/config") {
		t.Fatalf("workbench removed selected profile configuration or unrelated env: %v", env)
	}
	for _, forbidden := range []string{
		"GEMINI_API_KEY=stub", "GOOGLE_API_KEY=stub", "GOOGLE_APPLICATION_CREDENTIALS=stub", "GIT_CONFIG_GLOBAL=/host/config",
	} {
		if slices.Contains(env, forbidden) {
			t.Errorf("workbench passed through %s", forbidden)
		}
	}
}

func TestWorkbenchDoesNotShareWorkspaceTrust(t *testing.T) {
	home := t.TempDir()
	t.Setenv("MULTI_AGENT_CLI", "1")
	t.Setenv("AGYS_REAL_HOME", home)
	t.Setenv("AGYS_DIR", filepath.Join(home, ".agys"))
	alpha, err := GetProfileDir("alpha")
	if err != nil {
		t.Fatal(err)
	}
	beta, err := GetProfileDir("beta")
	if err != nil {
		t.Fatal(err)
	}
	alphaSettings := filepath.Join(alpha, ".gemini", "antigravity-cli", "settings.json")
	betaSettings := filepath.Join(beta, ".gemini", "antigravity-cli", "settings.json")
	if err := updateSettingsTrustedWorkspaces(alphaSettings, []string{"/project-alpha"}); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(beta, 0700); err != nil {
		t.Fatal(err)
	}
	if err := SyncTrustedWorkspaces(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(betaSettings); !os.IsNotExist(err) {
		t.Fatalf("workspace trust sync created beta settings: %v", err)
	}
}

func TestSyncTrustedWorkspaces(t *testing.T) {
	tempHome := t.TempDir()
	t.Setenv("HOME", tempHome)
	t.Setenv("AGYS_DIR", filepath.Join(tempHome, ".agys"))

	p1Dir, err := Create("profile-1")
	if err != nil {
		t.Fatalf("Create profile-1 error: %v", err)
	}
	p2Dir, err := Create("profile-2")
	if err != nil {
		t.Fatalf("Create profile-2 error: %v", err)
	}

	p1Settings := filepath.Join(p1Dir, ".gemini", "antigravity-cli", "settings.json")
	p2Settings := filepath.Join(p2Dir, ".gemini", "antigravity-cli", "settings.json")

	_ = updateSettingsTrustedWorkspaces(p1Settings, []string{"/path/project-A", "/path/project-B"})
	_ = updateSettingsTrustedWorkspaces(p2Settings, []string{"/path/project-B", "/path/project-C"})

	err = SyncTrustedWorkspaces()
	if err != nil {
		t.Fatalf("SyncTrustedWorkspaces error: %v", err)
	}

	trustedMap1 := make(map[string]bool)
	readSettingsWorkspaces(p1Settings, trustedMap1)

	trustedMap2 := make(map[string]bool)
	readSettingsWorkspaces(p2Settings, trustedMap2)

	expected := map[string]bool{
		"/path/project-A": true,
		"/path/project-B": true,
		"/path/project-C": true,
	}

	if !reflect.DeepEqual(trustedMap1, expected) {
		t.Errorf("Expected profile-1 trusted workspaces %v, got %v", expected, trustedMap1)
	}
	if !reflect.DeepEqual(trustedMap2, expected) {
		t.Errorf("Expected profile-2 trusted workspaces %v, got %v", expected, trustedMap2)
	}
}
