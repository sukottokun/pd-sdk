from dataclasses import dataclass
from typing import List, Dict

try:
    from typing import Protocol
except ImportError:
    from typing_extensions import Protocol  # type: ignore


import logging
from paralleldomain.model.scene import Scene, SceneDecoderProtocol
from paralleldomain.model.type_aliases import SceneName

logger = logging.getLogger(__name__)


@dataclass
class DatasetMeta:
    origin: str
    name: str
    creator: str
    available_annotation_types: List[int]
    creation_date: str
    version: str
    description: str


class DatasetDecoderProtocol(SceneDecoderProtocol, Protocol):

    def decode_scene_names(self) -> List[SceneName]:
        pass

    def decode_dataset_meta_data(self) -> DatasetMeta:
        pass


class Dataset:
    def __init__(self, meta_data: DatasetMeta, scene_names: List[SceneName], decoder: DatasetDecoderProtocol):
        self._decoder = decoder
        self._scenes: Dict[SceneName, Scene] = dict()
        self.meta_data = meta_data
        self._scene_names: List[SceneName] = scene_names

    def _load_scene(self, scene_name: SceneName):
        if scene_name not in self._scenes:
            self._scenes[scene_name] = Scene.from_decoder(scene_name=scene_name,
                                                          decoder=self._decoder)

    @property
    def scene_names(self) -> List[SceneName]:
        return self._scene_names

    @property
    def scenes(self) -> Dict[SceneName, Scene]:
        for scene_name in self._scene_names:
            self._load_scene(scene_name=scene_name)
        return self._scenes

    def get_scene(self, scene_name: SceneName) -> Scene:
        self._load_scene(scene_name=scene_name)
        return self._scenes[scene_name]

    @staticmethod
    def from_decoder(decoder: DatasetDecoderProtocol) -> "Dataset":
        scene_names = decoder.decode_scene_names()
        meta_data = decoder.decode_dataset_meta_data()
        return Dataset(meta_data=meta_data, scene_names=scene_names, decoder=decoder)