package cmd

import (
	"fmt"
	"os"
	"strings"

	"github.com/quaywin/agys/pkg/profile"
)

func resolveWorkbenchResume(name string, args []string, directory string) ([]string, error) {
	index, length, id, err := workbenchResumeArg(args)
	if err != nil || index < 0 {
		return args, err
	}
	if directory == "" {
		directory, err = os.Getwd()
		if err != nil {
			return args, err
		}
	}
	sessions, err := profile.WorkbenchSessions(name, directory)
	if err != nil {
		return args, err
	}
	for _, session := range sessions {
		if id == "" || id == session.ConvID {
			result := append([]string(nil), args[:index]...)
			result = append(result, "--conversation="+session.ConvID)
			return append(result, args[index+length:]...), nil
		}
	}
	if id == "" {
		return args, fmt.Errorf("no saved conversation for profile %q in the current project", name)
	}
	return args, fmt.Errorf("conversation %q does not belong to profile %q in the current project", id, name)
}

func workbenchResumeArg(args []string) (index, length int, id string, err error) {
	index = -1
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg == "--" {
			break
		}
		option, value, assigned := strings.Cut(strings.TrimLeft(arg, "-"), "=")
		if !strings.HasPrefix(arg, "-") {
			continue
		}
		var consumed int
		switch option {
		case "project", "new-project", "remote-agent":
			return -1, 0, "", fmt.Errorf("%s can override the workbench project", arg)
		case "conversation", "resume", "r":
			consumed = 1
			if !assigned && i+1 < len(args) && !strings.HasPrefix(args[i+1], "-") && args[i+1] != "" {
				consumed, value = 2, args[i+1]
			}
			if value == "" && (assigned || option == "conversation") {
				return -1, 0, "", fmt.Errorf("%s requires a conversation ID", arg)
			}
		case "c", "continue":
			if assigned && value != "false" && value != "true" {
				return -1, 0, "", fmt.Errorf("%s expects true or false", arg)
			}
			if assigned && value == "false" {
				continue
			}
			consumed = 1
			value = ""
		case "print", "prompt", "p", "model", "m", "effort", "output-format", "input-format",
			"i", "prompt-interactive", "add-dir", "agent", "json-schema", "log-file", "mode", "print-timeout", "settings":
			if !assigned {
				i++ // The next argument is a value, even if it resembles an option.
			}
		}
		if consumed > 0 {
			if index >= 0 {
				return -1, 0, "", fmt.Errorf("choose one conversation resume option")
			}
			index, length, id = i, consumed, value
			i += consumed - 1
		}
	}
	return index, length, id, nil
}
