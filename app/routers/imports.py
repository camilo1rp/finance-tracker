from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.domain.sources import CsvSource
from app.schemas import ImportResult, UnmappedValuesOut
from app.services.ingest_service import AccountNotFoundError, ingest_from_source

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("", response_model=ImportResult)
def import_csv(
    account_id: int,
    file: UploadFile,
    allow_duplicates: bool = False,
    db: Session = Depends(get_session),
) -> ImportResult:
    """
    Runs the full ingest pipeline for one CSV against one account's stored
    default_mapping. Delegates entirely to ingest_service.ingest_from_source;
    this function's only job is translating the HTTP upload into the
    source/fetch_kwargs the service expects.
    """
    try:
        result = ingest_from_source(
            db,
            account_id=account_id,
            source=CsvSource(),
            fetch_kwargs={"file_path": file.file},
            filename=file.filename or "upload.csv",
            allow_duplicates=allow_duplicates,
        )
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return ImportResult(
        account_id=result.account_id,
        import_batch_id=result.import_batch_id,
        total_rows_read=result.total_rows_read,
        inserted=result.inserted,
        duplicates_skipped=result.duplicates_skipped,
        unmapped=UnmappedValuesOut(
            transaction_types=result.unmapped.transaction_types,
            categories=result.unmapped.categories,
            owners=result.unmapped.owners,
            merchants=result.unmapped.merchants,
        ),
        errors=result.errors,
    )
