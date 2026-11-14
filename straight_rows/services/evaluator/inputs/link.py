from typing import Literal
from main import EvaluationResult

LinkType = Literal["link_down", "link_ok"]

def get_evaluation(link_history: list[LinkType]) -> EvaluationResult:
    """
    Evaluates the link history and returns an EvaluationResult.

    The evaluation is based on the link history:
    - If all links are "link_ok", all evaluation categories are set to 0.
    - If any link is "link_down", the equipment_fault category is set to 1, others remain 0.

    Args:
        link_history (list[LinkType]): A list of link statuses, either "link_down" or "link_ok".

    Returns:
        EvaluationResult: The evaluation result based on the link history.

    Raises:
        ValueError: If the link_history is empty.
    """

    if not link_history:
        raise ValueError("link_history cannot be empty")

    if all(link == "link_ok" for link in link_history):
        return EvaluationResult(
            space_weather=0,
            jamming=0,
            spoofing=0,
            equipment_fault=0,
            multi_path=0
        )
    else:
        return EvaluationResult(
            space_weather=0,
            jamming=0,
            spoofing=0,
            equipment_fault=1,
            multi_path=0
        )