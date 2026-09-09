"""Collection names and document shapes.

Documents are stored as plain dicts through PyMongo. These Pydantic models exist
to validate what the seed script writes and to document the shape for readers;
they are not an ORM.

Bilingual fields are stored side by side (`name_vi` / `name_en`) rather than in a
nested language map, because Atlas Search filters and sorts read them directly.
Each searchable document also carries an `embedding_source` field: one block of
text combining the fields worth searching on, in both languages. That single
field is what gets embedded, so a Vietnamese query and an English query hit the
same vector.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# --- Collection names ------------------------------------------------------
# Referenced everywhere instead of string literals so a rename is one edit.

PRODUCTS = "products"
POLICIES = "policies"
ORDERS = "orders"
CUSTOMERS = "customers"
CONVERSATIONS = "conversations"
EVENTS = "events"
HANDOFFS = "handoffs"


# --- Shared enums ----------------------------------------------------------

Language = Literal["vi", "en"]

PolicyType = Literal["return", "warranty", "shipping", "payment", "privacy"]

OrderStatus = Literal[
    "pending",
    "confirmed",
    "packing",
    "shipped",
    "out_for_delivery",
    "delivered",
    "cancelled",
    "returned",
]

Intent = Literal[
    "product_consultation",
    "policy_question",
    "order_tracking",
    "smalltalk",
    "out_of_scope",
    "human_request",
]


# --- Products --------------------------------------------------------------


class ProductAttribute(BaseModel):
    """One specification row.

    The key is a stable English identifier used by filters and tests. The label
    and value carry both languages, so an English-speaking shopper is not shown
    a Vietnamese specification sheet and vice versa.
    """

    key: str
    label_vi: str
    label_en: str
    value_vi: str
    value_en: str


class Product(BaseModel):
    sku: str
    name_vi: str
    name_en: str
    # `category` and `subcategory` are English slugs, not display text. They are
    # filter keys: the search tool exposes them as an enum the model chooses
    # from, and Atlas filters on them. Display labels live alongside.
    category: str
    category_vi: str
    category_en: str
    subcategory: str
    subcategory_vi: str
    subcategory_en: str
    brand: str
    price: int  # Vietnamese dong, stored as an integer. No sub-unit exists.
    sale_price: int | None = None
    currency: str = "VND"
    attributes: list[ProductAttribute] = Field(default_factory=list)
    description_vi: str
    description_en: str
    embedding_source: str
    rating: float
    review_count: int
    stock: int
    images: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @property
    def effective_price(self) -> int:
        return self.sale_price if self.sale_price is not None else self.price


# --- Policies --------------------------------------------------------------


class PolicyChunk(BaseModel):
    """One retrievable section of a policy document.

    `chunk_id` is what the agent cites, so it must be stable and human readable.
    Format: "<doc_id>#<section_slug>", e.g. "return-policy#refund-timing".
    """

    chunk_id: str
    doc_id: str
    policy_type: PolicyType
    title_vi: str
    title_en: str
    section_vi: str
    section_en: str
    chunk_index: int
    text_vi: str
    text_en: str
    embedding_source: str
    source_url: str
    updated_at: datetime


# --- Orders ----------------------------------------------------------------


class OrderItem(BaseModel):
    sku: str
    name_vi: str
    name_en: str
    quantity: int
    unit_price: int


class OrderTimelineEntry(BaseModel):
    status: OrderStatus
    at: datetime
    note_vi: str
    note_en: str


class Order(BaseModel):
    """An order.

    Contact details are stored hashed, never in the clear. `get_order_status`
    verifies a caller by hashing what they supply and comparing, so the tool can
    confirm identity without the database holding a readable phone number and
    without the model ever seeing one.
    """

    order_code: str
    customer_name: str
    phone_hash: str
    phone_last4: str  # Shown back to the user as a masked confirmation only.
    email_hash: str
    items: list[OrderItem]
    subtotal: int
    shipping_fee: int
    total: int
    status: OrderStatus
    timeline: list[OrderTimelineEntry]
    carrier: str | None = None
    tracking_code: str | None = None
    estimated_delivery: datetime | None = None
    created_at: datetime


# --- Conversations ---------------------------------------------------------


class StoredMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    # Raw Anthropic content blocks, kept so a resumed session replays the exact
    # history the model produced, including tool_use blocks.
    blocks: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    products: list[dict] = Field(default_factory=list)
    created_at: datetime


class Conversation(BaseModel):
    session_id: str
    lang: Language = "vi"
    messages: list[StoredMessage] = Field(default_factory=list)
    escalated: bool = False
    created_at: datetime
    updated_at: datetime


# --- Telemetry -------------------------------------------------------------


class TurnEvent(BaseModel):
    """One row per user turn. Feeds the admin dashboard."""

    session_id: str
    at: datetime
    lang: Language
    intent: Intent
    tools_used: list[str] = Field(default_factory=list)
    tool_rounds: int = 0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    grounded: bool = True
    blocked: bool = False
    escalated: bool = False
    error: str | None = None


# --- Human handoff ---------------------------------------------------------


class Handoff(BaseModel):
    ticket_id: str
    session_id: str
    reason: str
    summary: str
    lang: Language
    status: Literal["open", "assigned", "closed"] = "open"
    created_at: datetime
