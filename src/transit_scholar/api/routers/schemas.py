from fastapi import APIRouter, Depends, status
from transit_scholar.api.dependencies import get_product
from transit_scholar.api.errors import ApiError
from transit_scholar.api.schemas.schema_catalog import SchemaDraftRequest, SchemaResponse, SchemaValidationResponse
from transit_scholar.layer2.schema_catalog import SchemaCatalogError, SchemaNotFoundError, SchemaVersionExistsError

router = APIRouter(prefix="/api/v1")


@router.get("/schemas", response_model=list[SchemaResponse])
def list_schemas(product=Depends(get_product)):
    return [product.describe_schema(item) for item in product.schema_catalog.list()]


@router.post("/schemas/validate", response_model=SchemaValidationResponse)
def validate_schema(payload: SchemaDraftRequest, product=Depends(get_product)):
    valid, issues, _ = product.schema_catalog.validate(payload.model_dump())
    return SchemaValidationResponse(valid=valid, issues=issues)


@router.post("/schemas", response_model=SchemaResponse, status_code=status.HTTP_201_CREATED)
def create_schema(payload: SchemaDraftRequest, product=Depends(get_product)):
    try:
        return product.describe_schema(product.schema_catalog.create(payload.model_dump()))
    except SchemaVersionExistsError as exc:
        raise ApiError("SCHEMA_VERSION_EXISTS", "Schema version already exists", {}, 409) from exc
    except SchemaCatalogError as exc:
        raise ApiError("SCHEMA_INVALID", "Schema definition is invalid", {}, 422) from exc


@router.get("/schemas/{schema_id}/versions/{version}", response_model=SchemaResponse)
def get_schema(schema_id: str, version: str, product=Depends(get_product)):
    try:
        return product.describe_schema(product.schema_catalog.resolve(schema_id, version))
    except SchemaNotFoundError as exc:
        raise ApiError("NOT_FOUND", "Schema not found", {"schema_id": schema_id, "version": version}, 404) from exc
    except SchemaCatalogError as exc:
        raise ApiError("SCHEMA_INVALID", "Schema definition is invalid", {}, 422) from exc
