"""Go runner: `go test` against a throwaway module in a Go image."""

from web.services.runners.base import LanguageRunner


class GoRunner(LanguageRunner):
    name = "go"

    def image(self, settings) -> str:
        return settings.get("go_image", "golang:1.22-alpine")

    def filename(self) -> str:
        return "generated_test.go"

    def command(self, filename) -> list:
        # go test needs a module; init a throwaway one, then run the generated file.
        return ["sh", "-c",
                "cd /work && (test -f go.mod || go mod init runtest) && go test ./..."]
