"""Pure managed-sync request contract shared by Reader and FalcoType."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Sync(StrictModel):
    active: bool
    request_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    advancement: Literal["command", "status"] = "command"


# A chunk's part on one page. `carried` counts the characters of its parts on earlier
# pages; `last` marks the part that reaches the target, the only place a Chunk run ends.
class BookChunk(StrictModel):
    version: Literal[1]
    generation: int = Field(ge=1, le=9_007_199_254_740_991)
    preset: Literal["short", "medium", "long", "thicc"]
    book_id: str = Field(min_length=1, max_length=128)
    page_index: int = Field(ge=0)
    page_revision: int = Field(ge=1)
    start: int = Field(ge=0)
    end: int = Field(ge=1, le=50000)
    kind: Literal["text", "image", "scene", "empty"]
    characters: int = Field(ge=0, le=8 * 1024 * 1024)
    carried: int = Field(ge=0, le=8000)
    target: int | None
    last: bool

    @model_validator(mode="after")
    def whole_units(self):
        if self.end <= self.start:
            raise ValueError("A Book chunk requires a nonempty canonical range.")
        if self.kind != "text":
            if (self.characters or self.carried or self.last or self.target is not None
                    or (self.kind != "empty" and self.end != self.start + 1)):
                raise ValueError("Non-typing chunks have zero characters and no random target.")
        elif self.carried + self.characters < 1:
            raise ValueError("A text chunk requires typable characters.")
        else:
            bounds = {"short": (0, 450), "medium": (451, 1200), "long": (1201, 2800), "thicc": (2801, 8000)}
            low, high = bounds[self.preset]
            if self.target is None or not low <= self.target <= high:
                raise ValueError("Book chunk target is outside its preset range.")
            if self.carried and self.carried >= self.target:
                raise ValueError("A chunk that reached its target cannot be carried to another page.")
            if self.last != (self.carried + self.characters >= self.target):
                raise ValueError("Only the part that reaches the target is a chunk's last.")
        return self


class RunStart(StrictModel):
    book_id: str = Field(min_length=1, max_length=128)
    page_index: int = Field(ge=0)
    page_revision: int = Field(ge=1)
    sentence_index: int = Field(ge=0)
    grapheme_offset: int = Field(ge=0, le=8 * 1024 * 1024)


# Follow mode (Kokoro off): FalcoType moves through the book itself and the Reader
# only shows where it is. `id` grows with every new position. The Reader opens the
# first unit at or after it, past empty pages, and reports a book end when none is
# left. `epoch` is the Reader's own navigation count: a position from before the
# user moved the Reader is ignored.
class FollowPosition(StrictModel):
    epoch: int = Field(ge=0)
    id: int = Field(ge=1)
    book_id: str = Field(min_length=1, max_length=128)
    page_index: int = Field(ge=0)
    sentence_index: int = Field(ge=0)


class PracticeStatus(StrictModel):
    session_id: str = Field(min_length=1, max_length=128)
    selection_revision: int = Field(ge=0)
    sequence: int = Field(ge=1)
    run_state: Literal["preparing", "running", "paused", "finished"]
    typing_complete: bool
    user_paused: bool = False
    # user_paused repeats a pause made in the Reader, which may have lifted it since.
    pause_echo: bool = False
    limit_kind: Literal["finite", "unlimited", "chunk"] = "finite"
    remaining_ms: int | None = Field(ge=0, le=600_000_000)
    clock_paused: bool = False
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    run_start: RunStart | None = None
    outcome: Literal["time_limit", "chunk_end", "manual_stop", "book_end", "interrupted", "abandoned"] | None = None
    media_enabled: bool = True
    media_waiting: bool = False
    near_end: bool = False
    chunk: BookChunk | None = None
    follow: FollowPosition | None = None

    @model_validator(mode="after")
    def manual_pause_state(self):
        if self.follow and (self.media_waiting or self.near_end):
            raise ValueError("A followed run has no Reader media wait or speech handoff.")
        if (self.limit_kind != "finite") != (self.remaining_ms is None):
            raise ValueError("Non-timed limits require null remaining time; finite requires an integer.")
        if self.limit_kind == "chunk" and self.chunk is None:
            raise ValueError("A chunk limit requires its group identity.")
        if self.outcome == "chunk_end" and self.limit_kind != "chunk":
            raise ValueError("Chunk completion requires a chunk limit.")
        if self.run_start and not self.run_id:
            raise ValueError("A run checkpoint requires its run identity.")
        if self.outcome and (self.run_state != "finished" or not self.run_id):
            raise ValueError("A terminal outcome requires a finished identified run.")
        if self.outcome == "time_limit" and (self.limit_kind != "finite" or self.remaining_ms != 0):
            raise ValueError("Time expiry requires a finite exhausted limit.")
        if self.user_paused and self.run_state != "paused":
            raise ValueError("A user pause requires paused run state.")
        if self.media_waiting and (self.typing_complete or self.run_state == "finished"):
            raise ValueError("A media wait cannot complete typing or a finished run.")
        return self


class Target(StrictModel):
    page_index: int = Field(ge=0)
    sentence_index: int = Field(ge=0)


class TimeCut(StrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    mode: Literal["loop", "word", "skip"]
    chunk: BookChunk
    head: Target
    cursor: Target
    word_complete: bool = False

    @model_validator(mode="after")
    def chunk_head(self):
        if self.cursor.page_index != self.chunk.page_index or not self.chunk.start <= self.cursor.sentence_index < self.chunk.end:
            raise ValueError("The expired cursor must belong to its captured chunk.")
        if (self.head.page_index >= self.chunk.page_index if self.chunk.carried else
                (self.head.page_index, self.head.sentence_index) != (self.chunk.page_index, self.chunk.start)):
            raise ValueError("A time cut requires its captured chunk head.")
        return self


class TimeCutResult(StrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    status: Literal["pending", "applied"] = "applied"
    selection_revision: int = Field(ge=0)
    page_index: int = Field(ge=0)
    sentence_index: int = Field(ge=0)
    book_ended: bool = False
    skipped: bool = False


class RunAction(StrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    action: Literal["run_start", "chunk_start", "word", "next_chunk"]
    chunk: BookChunk
    cursor: Target
    # A carried chunk's head sits on an earlier page than chunk.start.
    head: Target | None = None
    word_complete: bool = False

    @model_validator(mode="after")
    def captured_cursor(self):
        if self.cursor.page_index != self.chunk.page_index or not self.chunk.start <= self.cursor.sentence_index < self.chunk.end:
            raise ValueError("Result cursor must belong to its captured chunk.")
        if self.action == "chunk_start" and self.chunk.carried and (not self.head or self.head.page_index >= self.chunk.page_index):
            raise ValueError("A carried chunk starts on an earlier page.")
        return self


class Command(StrictModel):
    id: int = Field(ge=1)
    session_id: str = Field(min_length=1, max_length=128)
    selection_revision: int = Field(ge=0)
    type: Literal["select", "prepare", "play", "pause", "resume", "complete", "advance", "continue", "skip_image", "media_next", "audio", "time_cut", "run_action"]
    target: Target | None = None
    audio_enabled: bool | None = None
    time_cut: TimeCut | None = None
    run_action: RunAction | None = None

    @model_validator(mode="after")
    def selection_target(self):
        if (self.type == "select") != (self.target is not None):
            raise ValueError("Only select commands require a target.")
        if (self.type == "audio") != (self.audio_enabled is not None):
            raise ValueError("Only audio commands require audio_enabled.")
        if (self.type == "time_cut") != (self.time_cut is not None):
            raise ValueError("Only time_cut commands require a time-cut intent.")
        if (self.type == "run_action") != (self.run_action is not None):
            raise ValueError("Only run_action commands require a result action.")
        return self
