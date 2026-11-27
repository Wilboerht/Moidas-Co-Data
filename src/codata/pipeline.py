from __future__ import annotations

import time
from typing import Any, Callable

from .api import XHS_COST
from .client import ProviderError
from .config import Settings
from .db import Database
from .matching import match_round_leads
from .normalize import utc_now
from .platforms import Registry, load_registry
from .providers import Provider, SearchResponse, build_provider, resolve_provider


class JobCancelled(RuntimeError):
    """Raised when another request cancels the active job."""


def _fatal_provider_error(exc: ProviderError) -> bool:
    return getattr(exc, "status", None) in {401, 402, 403} or getattr(exc, "code", None) == 1001


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        api: Any | None = None,
        client: Any | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.client = client
        self.api = api
        self.provider: Provider | None = None
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.provider is not None:
            self.provider.close()

    @staticmethod
    def estimate(
        keyword_count: int,
        pages: int,
        max_creators: int | None,
        unit_cost: int = XHS_COST,
    ) -> dict[str, int]:
        if keyword_count <= 0:
            return {"search_calls": 0, "user_calls": 0, "total_calls": 0, "tokens": 0}
        search_calls = max(0, keyword_count) * max(1, pages)
        user_calls = max_creators if max_creators is not None else search_calls * 10
        return {
            "search_calls": search_calls,
            "user_calls": max(0, user_calls),
            "total_calls": search_calls + max(0, user_calls),
            "tokens": (search_calls + max(0, user_calls)) * int(unit_cost),
        }

    def estimate_for(
        self,
        provider: Provider,
        keyword_count: int,
        pages: int,
        max_creators: int | None,
    ) -> dict[str, Any]:
        if keyword_count <= 0:
            return {
                "search_calls": 0,
                "user_calls": 0,
                "total_calls": 0,
                "tokens": 0,
                "provider": provider.provider_id,
            }
        search_calls = max(0, keyword_count) * max(1, pages)
        user_calls = max_creators if max_creators is not None else search_calls * 10
        total_calls = search_calls + max(0, user_calls)
        return {
            "search_calls": search_calls,
            "user_calls": max(0, user_calls),
            "total_calls": total_calls,
            "tokens": search_calls * provider.search_cost_tokens
            + max(0, user_calls) * provider.user_cost_tokens,
            "provider": provider.provider_id,
        }

    def run_round(
        self,
        round_id: int,
        job_id: int | None = None,
        refresh_users: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        round_row = self.db.get_round(round_id)
        if not round_row:
            raise ValueError(f"轮次不存在: {round_id}")
        if round_row.get("archived_at"):
            raise ValueError("已归档轮次不能运行采集，请先恢复")
        if job_id is None:
            job_id = self.db.claim_job(
                round_id,
                getattr(self.settings, "max_active_jobs", None),
                getattr(self.settings, "job_stale_minutes", 30),
            )
        current_job = self.db.get_job(job_id)
        if not current_job:
            raise ValueError(f"任务记录不存在: {job_id}")
        if int(current_job.get("round_id") or 0) != int(round_id):
            raise ValueError(f"任务 #{job_id} 不属于轮次 #{round_id}")
        if current_job.get("status") not in ("pending", "running"):
            raise JobCancelled("任务已结束，不能重复执行")
        self._active_job_id = job_id
        self._job_cost = self.db.sum_job_cost(job_id)
        self._round_cost = self.db.sum_round_cost(round_id)

        self.db.update_round(
            round_id,
            status="running",
            started_at=utc_now(),
            error=None,
            cost_tokens=self._round_cost,
        )
        self.db.update_job(
            job_id,
            status="running",
            stage="准备",
            started_at=utc_now(),
            error=None,
            cost=self._job_cost,
        )

        try:
            summary = self._run(round_id, round_row, job_id, refresh_users=refresh_users, force=force)
        except Exception as exc:
            current = self.db.get_job(job_id)
            cancelled = isinstance(exc, JobCancelled) or (
                current is not None and current.get("status") == "cancelled"
            )
            message = "任务已取消" if cancelled else str(exc)
            round_cost = self.db.sum_round_cost(round_id)
            job_cost = self.db.sum_job_cost(job_id)
            if current is None or current.get("status") in ("pending", "running"):
                self.db.finish_job(
                    job_id,
                    round_id,
                    status="cancelled" if cancelled else "failed",
                    stage="已取消" if cancelled else "失败",
                    error=message,
                    cost=job_cost,
                    round_cost=round_cost,
                    finished_at=utc_now(),
                )
            raise
        finally:
            self.close()
        return summary

    def _check_active(self, job_id: int) -> None:
        job = self.db.get_job(job_id)
        if not job:
            raise JobCancelled("任务记录不存在")
        if job.get("status") == "cancelled":
            raise JobCancelled("任务已取消")
        if job.get("status") not in ("pending", "running"):
            raise RuntimeError("任务已停止")
        self.db.update_job(job_id, heartbeat_at=utc_now())

    def _provider_candidates(self, platform: str, requested: str) -> list[Provider]:
        if self.api is not None:
            provider, _ = resolve_provider(self.settings, platform, requested, api=self.api)
            return [provider]
        registry: Registry = load_registry(self.settings)
        if requested not in {"auto", "cheapest", "default"}:
            option = registry.resolve(platform, requested)
            return [build_provider(self.settings, option, platform)]
        options = [option for option in registry.options(platform) if option.ready]
        options.sort(
            key=lambda option: (
                option.search.price.tokens(registry.usd_cny, registry.token_cny),
                list(registry.platform(platform).providers).index(option.provider.id),
            )
        )
        if not options:
            registry.resolve(platform, "auto")
        return [build_provider(self.settings, option, platform) for option in options]

    def _run(
        self,
        round_id: int,
        round_row: dict[str, Any],
        job_id: int,
        refresh_users: bool,
        force: bool,
    ) -> dict[str, Any]:
        keywords = self.db.get_round_keywords(round_id)
        if not keywords:
            raise ValueError("该轮次没有启用中的关键词")

        platform = str(round_row.get("platform") or "xhs")
        requested = str(round_row.get("provider") or "auto")
        pages = int(round_row.get("pages") or 1)
        max_creators = round_row.get("max_creators")
        max_creators = int(max_creators) if max_creators else None

        candidates = self._provider_candidates(platform, requested)
        if requested in {"auto", "cheapest", "default"} and len(candidates) > 1:
            candidates.sort(
                key=lambda candidate: self.estimate_for(
                    candidate, len(keywords), pages, max_creators
                )["tokens"]
            )
        provider: Provider | None = None
        estimate: dict[str, Any] = {}
        last_error: Exception | None = None
        for index, candidate in enumerate(candidates):
            candidate_estimate = self.estimate_for(candidate, len(keywords), pages, max_creators)
            try:
                self._check_balance(candidate_estimate, force=force, provider=candidate)
            except Exception as exc:
                last_error = exc
                candidate.close()
                if requested not in {"auto", "cheapest", "default"} or index == len(candidates) - 1:
                    raise
                continue
            provider = candidate
            estimate = candidate_estimate
            self.provider = candidate
            break
        if provider is None:
            raise last_error or ProviderError(None, "没有可用的采集服务商")
        for candidate in candidates:
            if candidate is not provider:
                try:
                    candidate.close()
                except Exception:
                    pass

        search_total = sum(int(row.get("pages") or pages) for row in keywords)
        self.db.update_job(
            job_id,
            stage="搜索笔记",
            progress_done=0,
            progress_total=search_total,
        )

        done = 0
        failures = 0
        search_failures = 0
        consecutive_failures = 0
        pending_hits: list[tuple[Any, ...]] = []
        for row in keywords:
            word = row["word"]
            keyword_pages = int(row.get("pages") or pages)
            search_state: dict[str, str] = {}
            for page in range(1, keyword_pages + 1):
                self._check_active(job_id)
                try:
                    response = self._call(
                        round_id,
                        platform,
                        "search",
                        lambda word=word, page=page, state=search_state: provider.search(word, page, state),
                        {"keyword": word, "page": page},
                        cost=provider.search_cost_tokens,
                        label=f"round{round_id}_{provider.provider_id}_search_{word}_{page}",
                        path=provider.search_path,
                        provider=provider.provider_id,
                        cost_native=provider.search_price,
                        provider_obj=provider,
                    )
                except ProviderError as exc:
                    failures += 1
                    search_failures += 1
                    consecutive_failures += 1
                    print(f"[search] {word} 第 {page} 页失败: {exc}")
                    if _fatal_provider_error(exc) or consecutive_failures >= 5:
                        raise
                    break
                if not isinstance(response, SearchResponse):
                    raise ProviderError(None, "搜索适配器返回了无效响应")
                items = response.items
                if response.state:
                    search_state = {
                        **search_state,
                        **{key: value for key, value in response.state.items() if value},
                    }
                with self.db.transaction():
                    for rank, item in enumerate(items, 1):
                        note, author = provider.normalize_search(item)
                        if not note.get("note_id"):
                            continue
                        if author:
                            self.db.upsert_creator_basic(author, commit=False)
                        self.db.upsert_note(note, commit=False)
                        if note.get("author_id"):
                            pending_hits.append(
                                (
                                    round_id,
                                    note["platform"],
                                    note["note_id"],
                                    word,
                                    page,
                                    rank,
                                )
                            )
                done += 1
                consecutive_failures = 0
                self.db.update_job(job_id, progress_done=done)
                print(f"[search] {word} 第 {page} 页: {len(items)} 条")
                if response.has_more is False or not items:
                    break

        if done == 0 and keywords:
            raise RuntimeError("所有搜索页均失败，未采集到任何数据")

        if not search_failures:
            with self.db.transaction():
                for hit in pending_hits:
                    self.db.add_note_hit(*hit, run_id=job_id, commit=False)

        stats = self.db.round_author_stats(round_id, run_id=job_id)
        to_fetch: list[dict[str, Any]] = []
        if not search_failures:
            for entry in stats:
                if max_creators is not None and len(to_fetch) >= max_creators:
                    break
                if not refresh_users and self.db.creator_is_fresh(
                    entry["platform"], entry["user_id"], self.settings.cache_days
                ):
                    continue
                to_fetch.append(entry)

        total_progress = done + len(to_fetch)
        self.db.update_job(
            job_id,
            stage="补全达人" if not search_failures else "搜索未完整，保留上一快照",
            progress_done=done,
            progress_total=total_progress,
        )

        enriched = 0
        for entry in to_fetch:
            self._check_active(job_id)
            platform_code = entry["platform"]
            user_id = entry["user_id"]
            profile_url = entry.get("profile_url") or ""
            try:
                payload = self._call(
                    round_id,
                    platform_code,
                    "user",
                    lambda user_id=user_id, profile_url=profile_url: provider.user(user_id, profile_url),
                    {"userId": user_id},
                    cost=provider.user_cost_tokens,
                    label=f"round{round_id}_{provider.provider_id}_user_{user_id}",
                    path=provider.user_path,
                    provider=provider.provider_id,
                    cost_native=provider.user_price,
                    provider_obj=provider,
                )
                creator = provider.normalize_user(payload, user_id)
                if not creator.get("user_id"):
                    creator["user_id"] = user_id
                self.db.upsert_creator(creator)
                enriched += 1
                consecutive_failures = 0
                print(f"[user] {creator.get('nickname') or user_id}: 粉丝 {creator.get('fans')}")
            except ProviderError as exc:
                failures += 1
                consecutive_failures += 1
                print(f"[user] {user_id} 失败: {exc}")
                if _fatal_provider_error(exc) or consecutive_failures >= 5:
                    raise
            except ValueError as exc:
                failures += 1
                consecutive_failures += 1
                print(f"[user] {user_id} 资料格式异常: {exc}")
                if consecutive_failures >= 5:
                    raise
            done += 1
            self.db.update_job(job_id, progress_done=done)

        self._check_active(job_id)
        snapshot_updated = search_failures == 0
        if snapshot_updated:
            self.db.prune_snapshot_creators(round_id, job_id)
            rows = self.db.refresh_creator_rounds(round_id, run_id=job_id)
            if max_creators is not None:
                self.db.prune_round_creators(round_id, max_creators)
                self.db.cleanup_orphans()
                rows = self.db.round_metrics(round_id)["creators"]
        else:
            rows = self.db.round_metrics(round_id)["creators"]
        lead_matches = {"matched": 0, "updated": 0, "ambiguous": 0}
        if getattr(self.settings, "lead_match_enabled", True):
            self._check_active(job_id)
            try:
                lead_matches = match_round_leads(self.db, round_id)
            except Exception as exc:
                print(f"[lead-match] 轮次 {round_id} 匹配线索失败: {exc}")
                try:
                    self.db.audit(
                        None,
                        "lead.match_failed",
                        f"round:{round_id}",
                        details={"error": str(exc)},
                    )
                except Exception:
                    pass
        self._check_active(job_id)
        round_cost = self.db.sum_round_cost(round_id)
        job_cost = self.db.sum_job_cost(job_id)
        if not failures:
            stage = "完成"
        elif not snapshot_updated:
            stage = f"完成（搜索失败 {search_failures} 次，保留上一快照）"
        else:
            stage = f"完成（失败 {failures} 次）"
        if snapshot_updated:
            self.db.update_round(round_id, latest_run_id=job_id)
        if not self.db.finish_job(
            job_id,
            round_id,
            status="done",
            stage=stage,
            progress_done=total_progress,
            cost=job_cost,
            round_cost=round_cost,
            finished_at=utc_now(),
        ):
            raise JobCancelled("任务已取消")
        self.db.update_round(
            round_id,
            provider=provider.provider_id if requested not in {"auto", "cheapest", "default"} else requested,
        )

        summary = {
            "round_id": round_id,
            "provider": provider.provider_id,
            "keywords": len(keywords),
            "notes": self.db.round_summary(round_id)["notes"],
            "authors": len(stats),
            "creators": rows,
            "enriched": enriched,
            "failed_calls": failures,
            "search_failed_calls": search_failures,
            "snapshot_updated": snapshot_updated,
            "failed_cost_estimate": self.db.sum_failed_estimate(job_id),
            "cost_tokens": round_cost,
            "job_cost_tokens": job_cost,
            "leads_matched": int(lead_matches.get("matched") or 0),
            "leads_updated": int(lead_matches.get("updated") or 0),
            "leads_ambiguous": int(lead_matches.get("ambiguous") or 0),
            "leads_suggested": int(lead_matches.get("suggested") or 0),
            "estimate": estimate,
        }
        print(
            f"[done] 轮次 {round_id} ({provider.label}): 关键词 {len(keywords)} 个, "
            f"作者 {len(stats)} 个, 补全 {enriched} 个, 线索匹配 {summary['leads_matched']} 个, "
            f"花费 {job_cost} token"
        )
        return summary

    def _check_balance(
        self,
        estimate: dict[str, Any],
        force: bool,
        provider: Provider,
    ) -> None:
        if not getattr(provider, "supports_balance", True):
            return
        needed_tokens = int(estimate["tokens"])
        if needed_tokens <= 0:
            return
        try:
            balance = provider.balance()
        except Exception as exc:
            if force:
                return
            raise RuntimeError(f"无法查询余额，已停止采集：{exc}") from exc
        if balance.remaining_tokens is None:
            if force:
                return
            raise RuntimeError(
                f"{provider.label} 余额接口未返回剩余额度，已停止采集；"
                "确认后可在任务详情勾选“跳过余额检查”重试。"
            )
        if balance.remaining_tokens < needed_tokens and not force:
            raise RuntimeError(
                f"{provider.label} 余额不足：{balance.text}，预计需要 {needed_tokens} token。"
                "请充值或确认余额后再试，或在任务详情勾选“跳过余额检查”。"
            )

    def _call(
        self,
        round_id: int,
        platform: str,
        kind: str,
        fn: Callable[[], Any],
        params: dict[str, Any],
        cost: int,
        label: str,
        path: str,
        provider: str = "dataflow",
        cost_native: Any = None,
        provider_obj: Any = None,
    ) -> Any:
        start = time.monotonic()
        try:
            result = fn()
        except ProviderError as exc:
            duration = int((time.monotonic() - start) * 1000)
            client = getattr(provider_obj, "client", None)
            attempts = getattr(client, "last_attempts", 0)
            charged_cost = int(getattr(client, "last_cost", 0) or 0)
            raw_file = None
            if exc.payload is not None:
                raw_file = self.db.save_raw(label + "_error", exc.payload)
            self.db.log_api_call(
                round_id,
                platform,
                kind,
                params,
                raw_file,
                exc.code,
                0,
                duration,
                path=path,
                job_id=getattr(self, "_active_job_id", None),
                provider=provider,
                cost_native=(
                    float(cost_native.amount) * max(1, int(attempts or 1))
                    if cost_native is not None
                    else 0
                ),
                cost_currency=cost_native.unit if cost_native is not None else "token",
                estimated_cost=charged_cost or cost * max(1, int(attempts or 1)),
                attempt=max(1, int(attempts or 1)),
                status="error",
                error=str(exc)[:1000],
            )
            raise
        duration = int((time.monotonic() - start) * 1000)
        payload = result.payload if isinstance(result, SearchResponse) else result
        raw_file = self.db.save_raw(label, payload)
        code = payload.get("code") if isinstance(payload, dict) else None
        if code is None:
            code = 200
        native_currency = cost_native.unit if cost_native is not None else "token"
        job_id = getattr(self, "_active_job_id", None)
        client = getattr(provider_obj, "client", None)
        attempts = getattr(client, "last_attempts", 0)
        native_amount = (
            float(cost_native.amount) * max(1, int(attempts or 1))
            if cost_native is not None
            else float(cost) * max(1, int(attempts or 1))
        )
        actual_cost = int(getattr(client, "last_cost", 0) or 0) or cost * max(1, int(attempts or 1))
        self.db.log_api_call(
            round_id,
            platform,
            kind,
            params,
            raw_file,
            code,
            actual_cost,
            duration,
            path=path,
            job_id=job_id,
            provider=provider,
            cost_native=native_amount,
            cost_currency=native_currency,
            estimated_cost=actual_cost,
            attempt=max(1, int(attempts or 1)),
            status="ok",
        )
        if job_id:
            self._job_cost = self.db.sum_job_cost(job_id)
            self._round_cost = self.db.sum_round_cost(round_id)
            self.db.update_job(job_id, cost=self._job_cost)
            self.db.update_round(round_id, cost_tokens=self._round_cost)
        return result
