from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple, TypeVar, Union, List

import numpy as np

import pd.state
from paralleldomain.common.constants import ANNOTATION_NAME_TO_CLASS
from paralleldomain.decoding.common import DecoderSettings
from paralleldomain.decoding.sensor_frame_decoder import CameraSensorFrameDecoder, LidarSensorFrameDecoder, T
from paralleldomain.decoding.step.common import get_sensor_rig_annotation_types
from paralleldomain.decoding.step.constants import PD_CLASS_DETAILS
from paralleldomain.model.annotation import (
    AnnotationTypes,
    Depth,
    InstanceSegmentation2D,
    InstanceSegmentation3D,
    SemanticSegmentation2D,
    SemanticSegmentation3D,
    SurfaceNormals2D,
    AnnotationIdentifier,
)
from paralleldomain.model.class_mapping import ClassMap
from paralleldomain.model.sensor import (
    CameraModel,
    CameraSensorFrame,
    LidarSensorFrame,
    RadarSensorFrame,
    SensorExtrinsic,
    SensorIntrinsic,
    SensorPose,
    SensorDataCopyTypes,
)
from paralleldomain.model.type_aliases import FrameId, SceneName, SensorName
from paralleldomain.utilities.any_path import AnyPath
from paralleldomain.utilities.coordinate_system import CoordinateSystem
from pd.data_lab.session_reference import TemporalSessionReference
from pd.session import StepSession
from pd.state import LidarSensorData, Pose6D, SensorData
from pd.state.state import PosedAgent, State

SensorFrameTypes = Union[CameraSensorFrame, RadarSensorFrame, LidarSensorFrame]
TDateTime = TypeVar("TDateTime", bound=Union[None, datetime])


class StepSensorFrameDecoder(CameraSensorFrameDecoder[TDateTime], LidarSensorFrameDecoder[TDateTime]):
    def __init__(
        self,
        session: TemporalSessionReference,
        sensor_rig: List[Union[pd.state.CameraSensor, pd.state.LiDARSensor]],
        dataset_name: str,
        scene_name: SceneName,
        settings: DecoderSettings,
        ego_agent_id: int,
        is_camera: bool,
    ):
        super().__init__(dataset_name=dataset_name, scene_name=scene_name, settings=settings)
        self._session = session
        self._sensor_rig = sensor_rig
        self._ego_agent_id = ego_agent_id
        self._is_camera = is_camera

    def camera_sensor(self, sensor_name: SensorName) -> pd.state.CameraSensor:
        cam = next(
            iter(
                [
                    sensor
                    for sensor in self._sensor_rig
                    if isinstance(sensor, pd.state.CameraSensor) and sensor.name == sensor_name
                ]
            ),
            None,
        )
        if cam is None:
            raise ValueError(f"Unknown camera with name {sensor_name}")
        return cam

    def lidar_sensor(self, sensor_name: SensorName) -> pd.state.LiDARSensor:
        cam = next(
            iter(
                [
                    sensor
                    for sensor in self._sensor_rig
                    if isinstance(sensor, pd.state.LiDARSensor) and sensor.name == sensor_name
                ]
            ),
            None,
        )
        if cam is None:
            raise ValueError(f"Unknown camera with name {sensor_name}")
        return cam

    @property
    def session(self) -> StepSession:
        if self._session.session is None:
            raise ValueError("This frame is not available anymore!")
        return self._session.session

    @property
    def state(self) -> State:
        if self._session.state is None:
            raise ValueError("This frame is not available anymore!")
        return self._session.state

    @lru_cache(maxsize=1)
    def _query_sensor_data(
        self, ego_id: int, sensor_name: str, frame_id: str
    ) -> Optional[Union[SensorData, LidarSensorData]]:
        return self.session.query_sensor_data(self._ego_agent_id, sensor_name, pd.state.SensorBuffer.RGB)

    def _decode_intrinsic(self, sensor_name: SensorName, frame_id: FrameId) -> SensorIntrinsic:
        cam = self.camera_sensor(sensor_name=sensor_name)

        if cam.distortion_params is not None:
            if cam.distortion_params.is_fisheye is True or cam.distortion_params.is_fisheye == 1:
                camera_model = CameraModel.OPENCV_FISHEYE
            elif cam.distortion_params.is_fisheye is False or cam.distortion_params.is_fisheye == 0:
                camera_model = CameraModel.OPENCV_PINHOLE
            elif cam.distortion_params.is_fisheye == 3:
                camera_model = CameraModel.PD_FISHEYE
            elif cam.distortion_params.is_fisheye == 6:
                camera_model = CameraModel.PD_ORTHOGRAPHIC
            else:
                camera_model = f"custom_{cam.distortion_params.is_fisheye}"

            return SensorIntrinsic(
                cx=cam.distortion_params.cx,
                cy=cam.distortion_params.cy,
                fx=cam.distortion_params.fx,
                fy=cam.distortion_params.fy,
                k1=cam.distortion_params.k1,
                k2=cam.distortion_params.k2,
                p1=cam.distortion_params.p1,
                p2=cam.distortion_params.p2,
                k3=cam.distortion_params.k3,
                k4=cam.distortion_params.k4,
                k5=cam.distortion_params.k5,
                k6=cam.distortion_params.k6,
                skew=cam.distortion_params.skew,
                fov=cam.field_of_view_degrees,
                camera_model=camera_model,
            )
        else:
            return SensorIntrinsic.from_field_of_view(
                field_of_view_degrees=cam.field_of_view_degrees, width=cam.width, height=cam.height
            )

    def _decode_image_dimensions(self, sensor_name: SensorName, frame_id: FrameId) -> Tuple[int, int, int]:
        cam = self.camera_sensor(sensor_name=sensor_name)
        return cam.height, cam.width, 3

    def _decode_image_rgba(self, sensor_name: SensorName, frame_id: FrameId) -> np.ndarray:
        sensor_data = self.session.query_sensor_data(self._ego_agent_id, sensor_name, pd.state.SensorBuffer.RGB)
        rgb_data = sensor_data.data_as_rgb
        if rgb_data.shape[-1] == 3:
            ones = np.ones((*rgb_data.shape[:2], 1), dtype=rgb_data.dtype) * 255
            concatenated = np.concatenate([rgb_data, ones], axis=-1)
            return concatenated
        elif rgb_data.shape[-1] == 1:  # grayscale fix for now
            return np.dstack([rgb_data.squeeze()] * 4)
        else:
            raise ValueError(f"Sensor returned {rgb_data.shape[1]} color channels. It must be either 3 or 1 channels.")

    def _decode_point_cloud_size(self, sensor_name: SensorName, frame_id: FrameId) -> int:
        pass

    def _decode_point_cloud_xyz(self, sensor_name: SensorName, frame_id: FrameId) -> Optional[np.ndarray]:
        sensor_data = self.session.query_sensor_data(self._ego_agent_id, sensor_name, pd.state.SensorBuffer.RGB)
        return sensor_data.data

    def _decode_point_cloud_rgb(self, sensor_name: SensorName, frame_id: FrameId) -> Optional[np.ndarray]:
        return None

    def _decode_point_cloud_intensity(self, sensor_name: SensorName, frame_id: FrameId) -> Optional[np.ndarray]:
        pass

    def _decode_point_cloud_timestamp(self, sensor_name: SensorName, frame_id: FrameId) -> Optional[np.ndarray]:
        pass

    def _decode_point_cloud_ring_index(self, sensor_name: SensorName, frame_id: FrameId) -> Optional[np.ndarray]:
        pass

    def _decode_point_cloud_ray_type(self, sensor_name: SensorName, frame_id: FrameId) -> Optional[np.ndarray]:
        pass

    def _decode_class_maps(self) -> Dict[AnnotationIdentifier, ClassMap]:
        available_annotations = get_sensor_rig_annotation_types(sensor_rig=self._sensor_rig)
        return {
            AnnotationIdentifier(annotation_type=anno_type): ClassMap(classes=PD_CLASS_DETAILS)
            for anno_type in ANNOTATION_NAME_TO_CLASS.values()
            if anno_type in available_annotations
        }

    def _decode_available_annotation_identifiers(
        self, sensor_name: SensorName, frame_id: FrameId
    ) -> List[AnnotationIdentifier]:
        annotation_identifiers: List[AnnotationIdentifier] = list()
        if self._is_camera:
            cam = self.camera_sensor(sensor_name=sensor_name)
            if cam.capture_depth:
                annotation_identifiers.append(AnnotationIdentifier(annotation_type=AnnotationTypes.Depth))
            if cam.capture_instances:
                annotation_identifiers.append(
                    AnnotationIdentifier(annotation_type=AnnotationTypes.InstanceSegmentation2D)
                )
            if cam.capture_segmentation:
                annotation_identifiers.append(
                    AnnotationIdentifier(annotation_type=AnnotationTypes.SemanticSegmentation2D)
                )
            if cam.capture_normals:
                annotation_identifiers.append(AnnotationIdentifier(annotation_type=AnnotationTypes.SurfaceNormals2D))
        # else:
        # lidar = self.lidar_sensor(sensor_name=sensor_name).to_step_sensor()
        # no annotations supported for LiDAR
        # if lidar.capture_instances:
        #     annotations[AnnotationTypes.InstanceSegmentation3D] = pd.state.SensorBuffer.INSTANCES
        # if lidar.capture_segmentation:
        #     annotations[AnnotationTypes.SemanticSegmentation3D] = pd.state.SensorBuffer.SEGMENTATION
        # if lidar.capture_normals:
        #     annotations[AnnotationTypes.SurfaceNormals3D] = pd.state.SensorBuffer.NORMALS
        return annotation_identifiers

    def _decode_metadata(self, sensor_name: SensorName, frame_id: FrameId) -> Dict[str, Any]:
        return dict()

    def _decode_date_time(self, sensor_name: SensorName, frame_id: FrameId) -> TDateTime:
        return datetime.fromtimestamp(self.state.simulation_time_sec)

    def _decode_extrinsic(self, sensor_name: SensorName, frame_id: FrameId) -> SensorExtrinsic:
        if self._is_camera:
            sensor = self.camera_sensor(sensor_name=sensor_name)
        else:
            sensor = self.lidar_sensor(sensor_name=sensor_name)

        if isinstance(sensor.pose, Pose6D):
            mat = sensor.pose.as_transformation_matrix()
        else:
            mat = sensor.pose

        RDF_to_RFU = CoordinateSystem("RDF") > CoordinateSystem("RFU")
        RFU_to_FLU = CoordinateSystem("RFU") > CoordinateSystem("FLU")

        sensor_to_ego_RFU = SensorExtrinsic.from_transformation_matrix(mat=mat, approximate_orthogonal=True)
        return RFU_to_FLU @ sensor_to_ego_RFU @ RDF_to_RFU

    def _decode_sensor_pose(self, sensor_name: SensorName, frame_id: FrameId) -> SensorPose:
        agent = next(
            iter([a for a in self.state.agents if a.id == self._ego_agent_id and isinstance(a, PosedAgent)]),
            None,
        )
        if agent is None:
            raise ValueError("No Ego Agent was set!")
        if isinstance(agent.pose, Pose6D):
            mat = agent.pose.as_transformation_matrix()
        else:
            mat = agent.pose

        sensor_to_ego = self.get_extrinsic(sensor_name=sensor_name, frame_id=frame_id)

        RFU_to_FLU = CoordinateSystem("RFU") > CoordinateSystem("FLU")

        ego_to_world = (
            RFU_to_FLU
            @ SensorPose.from_transformation_matrix(mat=mat, approximate_orthogonal=True)
            @ RFU_to_FLU.inverse
        )

        return ego_to_world @ sensor_to_ego

    def _decode_annotations(self, sensor_name: SensorName, frame_id: FrameId, identifier: AnnotationIdentifier[T]) -> T:
        if issubclass(identifier.annotation_type, SemanticSegmentation3D):
            sensor_data = self.session.query_sensor_data(
                self._ego_agent_id, sensor_name, pd.state.SensorBuffer.SEGMENTATION
            )
            class_ids = np.expand_dims(sensor_data.data_as_segmentation_ids, -1)
            return SemanticSegmentation3D(class_ids=class_ids.astype(int))
        elif issubclass(identifier.annotation_type, InstanceSegmentation3D):
            sensor_data = self.session.query_sensor_data(
                self._ego_agent_id, sensor_name, pd.state.SensorBuffer.INSTANCES
            )
            instance_ids = np.expand_dims(sensor_data.data_as_instance_ids, -1)
            return InstanceSegmentation3D(instance_ids=instance_ids.astype(int))
        elif issubclass(identifier.annotation_type, SemanticSegmentation2D):
            sensor_data = self.session.query_sensor_data(
                self._ego_agent_id, sensor_name, pd.state.SensorBuffer.SEGMENTATION
            )
            class_ids = np.expand_dims(sensor_data.data_as_segmentation_ids, -1)
            return SemanticSegmentation2D(class_ids=class_ids.astype(int))
        elif issubclass(identifier.annotation_type, InstanceSegmentation2D):
            sensor_data = self.session.query_sensor_data(
                self._ego_agent_id, sensor_name, pd.state.SensorBuffer.INSTANCES
            )
            instance_ids = np.expand_dims(sensor_data.data_as_instance_ids, -1)
            return InstanceSegmentation2D(instance_ids=instance_ids.astype(int))
        elif issubclass(identifier.annotation_type, Depth):
            sensor_data = self.session.query_sensor_data(self._ego_agent_id, sensor_name, pd.state.SensorBuffer.DEPTH)
            depth_mask = np.expand_dims(sensor_data.data_as_depth, -1)
            return Depth(depth=depth_mask)

        elif issubclass(identifier.annotation_type, SurfaceNormals2D):
            sensor_data = self.session.query_sensor_data(self._ego_agent_id, sensor_name, pd.state.SensorBuffer.NORMALS)
            normals = sensor_data.data[..., :3]
            return SurfaceNormals2D(normals=normals)

        else:
            raise NotImplementedError(f"{identifier} is not implemented yet in this decoder!")

    def _decode_file_path(
        self, sensor_name: SensorName, frame_id: FrameId, data_type: SensorDataCopyTypes
    ) -> Optional[AnyPath]:
        return None
