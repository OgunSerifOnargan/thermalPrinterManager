"""FastAPI dependency injection — wires services into routes."""
from __future__ import annotations

from fastapi import Request

from app.services.connection_manager import ConnectionManager
from app.services.eta import EtaService
from app.services.job_repository import JobRepository
from app.services.prediction import PaperPredictor
from app.services.printer_service import PrinterService
from app.services.reconcile_loop import ReconcileLoop


def get_manager(request: Request) -> ConnectionManager:
    return request.app.state.manager


def get_loop(request: Request) -> ReconcileLoop:
    return request.app.state.reconcile_loop


def get_service(request: Request) -> PrinterService:
    return request.app.state.printer_service


def get_repository(request: Request) -> JobRepository:
    return request.app.state.job_repository


def get_predictor(request: Request) -> PaperPredictor:
    return request.app.state.predictor


def get_eta(request: Request) -> EtaService:
    return request.app.state.eta_service
