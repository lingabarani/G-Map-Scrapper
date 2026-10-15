from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class PlaceStub(BaseModel):
    cid: str
    ftid: str
    place_id: str
    name: str
    address: Optional[str] = None
    street_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    phone_e164: Optional[str] = None
    category: Optional[str] = None
    vertical: Optional[str] = None
    vertical_raw: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    rating: Optional[float] = None
    review_count: int = 0
    status: str = "pending"

class PlaceHoursModel(BaseModel):
    day: int  # 1=Monday ... 7=Sunday
    day_name: str
    formatted_text: str
    is_closed: bool = False

class PlaceAttributeModel(BaseModel):
    group_id: str
    group_name: str
    attribute_id: str
    attribute_name: str
    is_enabled: bool = True
    display_value: Optional[str] = None

class ReviewModel(BaseModel):
    review_id: str
    cid: str
    author_name: str
    author_id: Optional[str] = None
    author_avatar_url: Optional[str] = None
    author_profile_url: Optional[str] = None
    rating: int
    relative_date: Optional[str] = None
    timestamp_ms: Optional[int] = None
    text: Optional[str] = None
    language: Optional[str] = None
    owner_response_text: Optional[str] = None
    owner_response_date: Optional[str] = None
    reaction_count: int = 0
    photos: List[str] = Field(default_factory=list)

class PhotoModel(BaseModel):
    cid: str
    photo_url: str
    review_id: Optional[str] = None
    local_path: Optional[str] = None
    attribution: Optional[str] = None
    file_size: Optional[int] = None

class PopularTimeModel(BaseModel):
    cid: str
    day_idx: int
    hour_idx: int
    popularity_pct: int

class PlaceDetailModel(BaseModel):
    cid: str
    ftid: str
    place_id: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    street_address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    website: Optional[str] = None
    phone: Optional[str] = None
    category: Optional[str] = None
    vertical: Optional[str] = None
    vertical_raw: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    timezone: Optional[str] = None
    claimed_status: Optional[int] = None
    photo_thumbnail: Optional[str] = None
    hours: List[PlaceHoursModel] = Field(default_factory=list)
    attributes: List[PlaceAttributeModel] = Field(default_factory=list)
    popular_times: List[PopularTimeModel] = Field(default_factory=list)
    photos: List[PhotoModel] = Field(default_factory=list)
