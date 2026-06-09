from pydantic import BaseModel, computed_field


class PaginationMeta(BaseModel):
    """Typed pagination metadata included in every paginated API response."""

    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_prev: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_empty(self) -> bool:
        return self.total == 0

    @classmethod
    def build(cls, total: int, page: int, page_size: int) -> "PaginationMeta":
        total_pages = max(1, (total + page_size - 1) // page_size)
        return cls(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_next=page * page_size < total,
            has_prev=page > 1,
        )
