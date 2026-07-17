from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppError(Exception):
    status_code: int
    code: str
    message: str
    user_message: str

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def payload(self) -> dict[str, dict[str, str]]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "user_message": self.user_message,
            }
        }

