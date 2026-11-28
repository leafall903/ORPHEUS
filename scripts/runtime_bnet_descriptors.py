"""Generate a clean-room Battle.net FileDescriptorSet covering the RPC messages used by ORPHEUS."""

from pathlib import Path

# google.protobuf.FieldDescriptorProto.Label
LABEL_OPTIONAL = 1
LABEL_REQUIRED = 2
LABEL_REPEATED = 3

# google.protobuf.FieldDescriptorProto.Type
TYPE_DOUBLE = 1
TYPE_INT64 = 3
TYPE_UINT64 = 4
TYPE_INT32 = 5
TYPE_FIXED64 = 6
TYPE_FIXED32 = 7
TYPE_BOOL = 8
TYPE_STRING = 9
TYPE_MESSAGE = 11
TYPE_BYTES = 12
TYPE_UINT32 = 13
TYPE_ENUM = 14


def _varint(value: int) -> bytes:
    if value < 0:
        value &= (1 << 64) - 1
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def _tag(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _varint_field(field_number: int, value: int) -> bytes:
    return _tag(field_number, 0) + _varint(value)


def _bytes_field(field_number: int, payload: bytes) -> bytes:
    return _tag(field_number, 2) + _varint(len(payload)) + payload


def _str_field(field_number: int, text: str) -> bytes:
    return _bytes_field(field_number, text.encode("utf-8"))


def _field_proto(
    name: str,
    number: int,
    label: int,
    field_type: int,
    type_name: str = "",
    default_value: str | None = None,
    packed: bool = False,
) -> bytes:
    parts = [
        _str_field(1, name),
        _varint_field(3, number),
        _varint_field(4, label),
        _varint_field(5, field_type),
    ]
    if type_name:
        parts.append(_str_field(6, type_name))
    if default_value is not None:
        parts.append(_str_field(7, default_value))
    if packed:
        parts.append(_bytes_field(8, _varint_field(2, 1)))
    return b"".join(parts)


def _enum_proto(name: str, values: list[tuple[str, int]]) -> bytes:
    parts = [_str_field(1, name)]
    for val_name, number in values:
        val_msg = _str_field(1, val_name) + _varint_field(2, number)
        parts.append(_bytes_field(2, val_msg))
    return b"".join(parts)


def _message_proto(name: str, fields: list[tuple]) -> bytes:
    parts = [_str_field(1, name)]
    for spec in fields:
        parts.append(_bytes_field(2, _field_proto(*spec)))
    return b"".join(parts)


def _file_proto(
    name: str,
    package: str,
    dependencies: list[str] | None = None,
    messages: list[bytes] | None = None,
    enums: list[bytes] | None = None,
) -> bytes:
    parts = [_str_field(1, name), _str_field(2, package)]
    for dep in dependencies or ():
        parts.append(_str_field(3, dep))
    for msg in messages or ():
        parts.append(_bytes_field(4, msg))
    for enum in enums or ():
        parts.append(_bytes_field(5, enum))
    return b"".join(parts)


def build_bnet_descriptor_set() -> bytes:
    """Return serialized google.protobuf.FileDescriptorSet for ORPHEUS's Battle.net RPC subset."""
    f_rpc = _file_proto(
        "orpheus/bgs/rpc_types.proto",
        "bgs.protocol",
        messages=[
            _message_proto("ProcessId", [
                ("label", 1, LABEL_REQUIRED, TYPE_UINT32),
                ("epoch", 2, LABEL_REQUIRED, TYPE_UINT32),
            ]),
            _message_proto("EntityId", [
                ("high", 1, LABEL_REQUIRED, TYPE_FIXED64),
                ("low", 2, LABEL_REQUIRED, TYPE_FIXED64),
            ]),
            _message_proto("ObjectAddress", [
                ("host", 1, LABEL_REQUIRED, TYPE_MESSAGE, ".bgs.protocol.ProcessId"),
                ("object_id", 2, LABEL_OPTIONAL, TYPE_UINT64),
            ]),
            _message_proto("ErrorInfo", [
                ("object_address", 1, LABEL_REQUIRED, TYPE_MESSAGE, ".bgs.protocol.ObjectAddress"),
                ("status", 2, LABEL_REQUIRED, TYPE_UINT32),
                ("service_hash", 3, LABEL_REQUIRED, TYPE_UINT32),
                ("method_id", 4, LABEL_REQUIRED, TYPE_UINT32),
            ]),
            _message_proto("FanoutTarget", [
                ("client_id", 1, LABEL_OPTIONAL, TYPE_STRING),
                ("key", 2, LABEL_OPTIONAL, TYPE_BYTES),
                ("object_id", 3, LABEL_OPTIONAL, TYPE_UINT64),
            ]),
            _message_proto("Header", [
                ("service_id", 1, LABEL_REQUIRED, TYPE_UINT32),
                ("method_id", 2, LABEL_OPTIONAL, TYPE_UINT32),
                ("token", 3, LABEL_REQUIRED, TYPE_UINT32),
                ("object_id", 4, LABEL_OPTIONAL, TYPE_UINT64, "", "0"),
                ("size", 5, LABEL_OPTIONAL, TYPE_UINT32, "", "0"),
                ("status", 6, LABEL_OPTIONAL, TYPE_UINT32, "", "0"),
                ("error", 7, LABEL_REPEATED, TYPE_MESSAGE, ".bgs.protocol.ErrorInfo"),
                ("timeout", 8, LABEL_OPTIONAL, TYPE_UINT64),
                ("is_response", 9, LABEL_OPTIONAL, TYPE_BOOL),
                ("forward_targets", 10, LABEL_REPEATED, TYPE_MESSAGE, ".bgs.protocol.ProcessId"),
                ("service_hash", 11, LABEL_OPTIONAL, TYPE_FIXED32),
                ("client_id", 13, LABEL_OPTIONAL, TYPE_STRING),
                ("fanout_target", 14, LABEL_REPEATED, TYPE_MESSAGE, ".bgs.protocol.FanoutTarget"),
                ("client_id_fanout_target", 15, LABEL_REPEATED, TYPE_STRING),
                ("client_record", 16, LABEL_OPTIONAL, TYPE_BYTES),
                ("original_sender", 17, LABEL_OPTIONAL, TYPE_BYTES),
                ("sender_token", 18, LABEL_OPTIONAL, TYPE_UINT32),
                ("router_label", 19, LABEL_OPTIONAL, TYPE_UINT32),
                ("error_reason", 20, LABEL_OPTIONAL, TYPE_STRING),
            ]),
        ],
    )

    f_attr_v2 = _file_proto(
        "orpheus/bgs/attribute_types_v2.proto",
        "bgs.protocol.v2",
        messages=[
            _message_proto("Variant", [
                ("bool_value", 1, LABEL_OPTIONAL, TYPE_BOOL),
                ("int_value", 2, LABEL_OPTIONAL, TYPE_INT64),
                ("float_value", 3, LABEL_OPTIONAL, TYPE_DOUBLE),
                ("string_value", 4, LABEL_OPTIONAL, TYPE_STRING),
                ("blob_value", 5, LABEL_OPTIONAL, TYPE_BYTES),
                ("uint_value", 6, LABEL_OPTIONAL, TYPE_UINT64),
            ]),
            _message_proto("Attribute", [
                ("name", 1, LABEL_OPTIONAL, TYPE_STRING),
                ("value", 2, LABEL_OPTIONAL, TYPE_MESSAGE, ".bgs.protocol.v2.Variant"),
            ]),
        ],
    )

    f_conn = _file_proto(
        "orpheus/bgs/connection_service.proto",
        "bgs.protocol.connection.v1",
        dependencies=["orpheus/bgs/rpc_types.proto"],
        enums=[
            _enum_proto("MeteringLevel", [
                ("METERING_LEVEL_LEGACY", 0),
                ("METERING_LEVEL_CATEGORIZED", 1),
            ]),
        ],
        messages=[
            _message_proto("BoundService", [
                ("hash", 1, LABEL_REQUIRED, TYPE_FIXED32),
                ("id", 2, LABEL_REQUIRED, TYPE_UINT32),
            ]),
            _message_proto("BindRequest", [
                ("deprecated_imported_service_hash", 1, LABEL_REPEATED, TYPE_FIXED32, "", None, True),
                ("deprecated_exported_service", 2, LABEL_REPEATED, TYPE_MESSAGE, ".bgs.protocol.connection.v1.BoundService"),
                ("exported_service", 3, LABEL_REPEATED, TYPE_MESSAGE, ".bgs.protocol.connection.v1.BoundService"),
                ("imported_service", 4, LABEL_REPEATED, TYPE_MESSAGE, ".bgs.protocol.connection.v1.BoundService"),
            ]),
            _message_proto("BindResponse", [
                ("imported_service_id", 1, LABEL_REPEATED, TYPE_UINT32, "", None, True),
            ]),
            _message_proto("ClientSdkVersionInfo", [
                ("sdk_name", 1, LABEL_OPTIONAL, TYPE_STRING),
                ("sdk_version", 2, LABEL_OPTIONAL, TYPE_STRING),
                ("protocol_version", 3, LABEL_OPTIONAL, TYPE_STRING),
            ]),
            _message_proto("ConnectionMeteringContentHandles", [
                ("content_handle", 1, LABEL_REPEATED, TYPE_BYTES),
            ]),
            _message_proto("ConnectRequest", [
                ("client_id", 1, LABEL_OPTIONAL, TYPE_MESSAGE, ".bgs.protocol.ProcessId"),
                ("bind_request", 2, LABEL_OPTIONAL, TYPE_MESSAGE, ".bgs.protocol.connection.v1.BindRequest"),
                ("use_bindless_rpc", 3, LABEL_OPTIONAL, TYPE_BOOL, "", "true"),
                ("metering_level", 4, LABEL_OPTIONAL, TYPE_ENUM, ".bgs.protocol.connection.v1.MeteringLevel"),
                ("client_sdk_info", 5, LABEL_OPTIONAL, TYPE_MESSAGE, ".bgs.protocol.connection.v1.ClientSdkVersionInfo"),
            ]),
            _message_proto("ConnectResponse", [
                ("server_id", 1, LABEL_REQUIRED, TYPE_MESSAGE, ".bgs.protocol.ProcessId"),
                ("client_id", 2, LABEL_OPTIONAL, TYPE_MESSAGE, ".bgs.protocol.ProcessId"),
                ("bind_result", 3, LABEL_OPTIONAL, TYPE_UINT32),
                ("bind_response", 4, LABEL_OPTIONAL, TYPE_MESSAGE, ".bgs.protocol.connection.v1.BindResponse"),
                ("content_handle_array", 5, LABEL_OPTIONAL, TYPE_MESSAGE, ".bgs.protocol.connection.v1.ConnectionMeteringContentHandles"),
                ("server_time", 6, LABEL_OPTIONAL, TYPE_UINT64),
                ("use_bindless_rpc", 7, LABEL_OPTIONAL, TYPE_BOOL, "", "false"),
                ("binary_content_handle_array", 8, LABEL_OPTIONAL, TYPE_MESSAGE, ".bgs.protocol.connection.v1.ConnectionMeteringContentHandles"),
                ("ciid", 9, LABEL_OPTIONAL, TYPE_STRING),
            ]),
        ],
    )

    f_auth = _file_proto(
        "orpheus/bgs/authentication_service.proto",
        "bgs.protocol.authentication.v1",
        dependencies=["orpheus/bgs/rpc_types.proto"],
        messages=[
            _message_proto("LogonRequest", [
                ("program", 1, LABEL_OPTIONAL, TYPE_STRING),
                ("platform", 2, LABEL_OPTIONAL, TYPE_STRING),
                ("locale", 3, LABEL_OPTIONAL, TYPE_STRING),
                ("email", 4, LABEL_OPTIONAL, TYPE_STRING),
                ("version", 5, LABEL_OPTIONAL, TYPE_STRING),
                ("application_version", 6, LABEL_OPTIONAL, TYPE_INT32),
                ("public_computer", 7, LABEL_OPTIONAL, TYPE_BOOL),
                ("allow_logon_queue_notifications", 10, LABEL_OPTIONAL, TYPE_BOOL),
                ("cached_web_credentials", 12, LABEL_OPTIONAL, TYPE_BYTES),
                ("user_agent", 14, LABEL_OPTIONAL, TYPE_STRING),
                ("device_id", 15, LABEL_OPTIONAL, TYPE_STRING),
                ("phone_number", 16, LABEL_OPTIONAL, TYPE_STRING),
            ]),
            _message_proto("LogonResult", [
                ("error_code", 1, LABEL_REQUIRED, TYPE_UINT32),
                ("account_id", 2, LABEL_OPTIONAL, TYPE_MESSAGE, ".bgs.protocol.EntityId"),
                ("game_account_id", 3, LABEL_REPEATED, TYPE_MESSAGE, ".bgs.protocol.EntityId"),
                ("email", 4, LABEL_OPTIONAL, TYPE_STRING),
                ("available_region", 5, LABEL_REPEATED, TYPE_UINT32),
                ("connected_region", 6, LABEL_OPTIONAL, TYPE_UINT32),
                ("battle_tag", 7, LABEL_OPTIONAL, TYPE_STRING),
                ("geoip_country", 8, LABEL_OPTIONAL, TYPE_STRING),
                ("session_key", 9, LABEL_OPTIONAL, TYPE_BYTES),
                ("restricted_mode", 10, LABEL_OPTIONAL, TYPE_BOOL),
                ("client_id", 11, LABEL_OPTIONAL, TYPE_STRING),
            ]),
        ],
    )

    f_gu = _file_proto(
        "orpheus/bgs/game_utilities_service_v2.proto",
        "bgs.protocol.game_utilities.v2.client",
        dependencies=["orpheus/bgs/attribute_types_v2.proto"],
        messages=[
            _message_proto("ProcessTaskRequest", [
                ("attribute", 1, LABEL_REPEATED, TYPE_MESSAGE, ".bgs.protocol.v2.Attribute"),
                ("payload", 2, LABEL_REPEATED, TYPE_MESSAGE, ".bgs.protocol.v2.Attribute"),
            ]),
            _message_proto("ProcessTaskResponse", [
                ("result", 1, LABEL_REPEATED, TYPE_MESSAGE, ".bgs.protocol.v2.Attribute"),
            ]),
        ],
    )

    return b"".join(_bytes_field(1, file_pb) for file_pb in (f_rpc, f_attr_v2, f_conn, f_auth, f_gu))


def write_bnet_descriptors(output: Path) -> int:
    data = build_bnet_descriptor_set()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return len(data)
