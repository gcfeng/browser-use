from dataclasses import dataclass
from typing import Callable, Optional, Type

from pydantic import BaseModel, ConfigDict, Field


@dataclass
class ActionInputs:
	content: Optional[str] = None
	start_box: Optional[str] = None
	end_box: Optional[str] = None
	key: Optional[str] = None
	hotkey: Optional[str] = None
	direction: Optional[str] = None
	start_coords: Optional[list[float]] = None
	end_coords: Optional[list[float]] = None


@dataclass
class PredictionParsed:
	action_inputs: ActionInputs
	action_type: str
	thought: str = ''
	reflection: Optional[str] = None


class RegisteredUITarsAction(BaseModel):
	"""Model for a registered action"""

	name: str
	description: str
	function: Callable
	param_model: Type[BaseModel]

	model_config = ConfigDict(arbitrary_types_allowed=True)


class UITarsActionModel(BaseModel):
	model_config = ConfigDict(arbitrary_types_allowed=True)


class UITarsOutputModel(BaseModel):
	model_config = ConfigDict(arbitrary_types_allowed=True)
	action: list[UITarsActionModel] = Field(
		...,
		description='List of actions to execute',
		json_schema_extra={'min_items': 1},  # Ensure at least one action is provided
	)
