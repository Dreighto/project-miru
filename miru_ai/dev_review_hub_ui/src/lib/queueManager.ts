/**
 * Operator console queue state manager.
 * Handles priority ordering, background replenishment, and dedup.
 */

import type { QueueItem } from "@/data/mockQueue";

// Extended item with operator-console enrichment fields.
export interface OperatorQueueItem extends QueueItem {
  state: "live" | "staged";
  issues: string[];
  contextSentence: string;
  candidateStatus?: string;
  elevationReason?: string;
  reconciliationStatus?: string | null;
  /** ISO timestamp — card is cooling after a recent reject/hold. */
  coolingUntil?: string | null;
}

export interface QueueState {
  items: OperatorQueueItem[];
  offset: number;
  hasMore: boolean;
  isFetching: boolean;
  setCode: string;
  seenIds: Set<string>;
  /** Card ID currently being acted on (dim + spinner). */
  pendingCardId: string | null;
  /** Card ID with a conflict banner showing. */
  conflictCardId: string | null;
  conflictBanner: string | null;
}

export type QueueAction =
  | { type: "LOAD"; items: OperatorQueueItem[]; hasMore: boolean }
  | { type: "APPEND"; items: OperatorQueueItem[]; hasMore: boolean }
  | { type: "REMOVE"; cardId: string }
  | { type: "SET_PENDING"; cardId: string }
  | { type: "CLEAR_PENDING"; cardId: string }
  | { type: "SET_CONFLICT"; cardId: string; banner: string }
  | { type: "CLEAR_CONFLICT"; cardId: string }
  | { type: "SET_FETCHING"; isFetching: boolean }
  | { type: "CHANGE_SET"; setCode: string };

export function initialQueueState(setCode = "OP01"): QueueState {
  return {
    items: [],
    offset: 0,
    hasMore: false,
    isFetching: false,
    setCode,
    seenIds: new Set(),
    pendingCardId: null,
    conflictCardId: null,
    conflictBanner: null,
  };
}

export function queueReducer(state: QueueState, action: QueueAction): QueueState {
  switch (action.type) {
    case "LOAD": {
      const seen = new Set<string>();
      const items: OperatorQueueItem[] = [];
      for (const item of action.items) {
        if (!seen.has(item.id)) {
          seen.add(item.id);
          items.push(item);
        }
      }
      return {
        ...state,
        items: sortByPriority(items),
        offset: items.length,
        hasMore: action.hasMore,
        seenIds: seen,
        isFetching: false,
        pendingCardId: null,
        conflictCardId: null,
        conflictBanner: null,
      };
    }
    case "APPEND": {
      const newItems: OperatorQueueItem[] = [];
      const seen = new Set(state.seenIds);
      for (const item of action.items) {
        if (!seen.has(item.id)) {
          seen.add(item.id);
          newItems.push(item);
        }
      }
      return {
        ...state,
        items: sortByPriority([...state.items, ...newItems]),
        offset: state.offset + newItems.length,
        hasMore: action.hasMore,
        seenIds: seen,
        isFetching: false,
      };
    }
    case "REMOVE":
      return {
        ...state,
        items: state.items.filter((i) => i.id !== action.cardId),
        pendingCardId: state.pendingCardId === action.cardId ? null : state.pendingCardId,
        conflictCardId: state.conflictCardId === action.cardId ? null : state.conflictCardId,
        conflictBanner: state.conflictCardId === action.cardId ? null : state.conflictBanner,
      };
    case "SET_PENDING":
      return { ...state, pendingCardId: action.cardId, conflictCardId: null, conflictBanner: null };
    case "CLEAR_PENDING":
      return { ...state, pendingCardId: state.pendingCardId === action.cardId ? null : state.pendingCardId };
    case "SET_CONFLICT":
      return {
        ...state,
        pendingCardId: null,
        conflictCardId: action.cardId,
        conflictBanner: action.banner,
      };
    case "CLEAR_CONFLICT":
      return {
        ...state,
        conflictCardId: state.conflictCardId === action.cardId ? null : state.conflictCardId,
        conflictBanner: state.conflictCardId === action.cardId ? null : state.conflictBanner,
      };
    case "SET_FETCHING":
      return { ...state, isFetching: action.isFetching };
    case "CHANGE_SET":
      return initialQueueState(action.setCode);
    default:
      return state;
  }
}

const REPLENISH_WATERMARK = 3;
const FETCH_BATCH = 10;

/** Check whether replenishment should fire. */
export function shouldReplenish(state: QueueState): boolean {
  return state.hasMore && !state.isFetching && state.items.length <= REPLENISH_WATERMARK;
}

/** Fetch a batch from the queue API. */
export async function fetchQueueBatch(
  setCode: string,
  offset: number,
  limit: number = FETCH_BATCH,
): Promise<{ items: OperatorQueueItem[]; hasMore: boolean }> {
  const params = new URLSearchParams({
    set_code: setCode,
    offset: String(offset),
    limit: String(limit),
  });
  const res = await fetch(`/api/dev/training-review/queue?${params}`, {
    credentials: "same-origin",
    headers: { Accept: "application/json", "X-Requested-With": "miru-client-nav" },
  });
  const data = await res.json();
  return {
    items: Array.isArray(data.items) ? data.items : [],
    hasMore: Boolean(data.hasMore),
  };
}

/**
 * Sort queue items by priority:
 * 1. Elevated live first
 * 2. Elevated staged second
 * 3. Oldest stale third (items with issues or contradictions)
 * 4. Everything else by natural order
 */
function sortByPriority(items: OperatorQueueItem[]): OperatorQueueItem[] {
  return [...items].sort((a, b) => {
    const pa = priorityScore(a);
    const pb = priorityScore(b);
    return pa - pb;
  });
}

function priorityScore(item: OperatorQueueItem): number {
  // Cooling items always sort to the bottom.
  if (item.coolingUntil) return 100;

  const isElevated =
    item.candidateStatus === "ELEVATED_REVIEW_REQUIRED" ||
    item.reconciliationStatus === "CONTRADICTED";
  const isLive = item.state === "live";
  const hasIssues = item.issues.length > 0;

  if (isElevated && isLive) return 0;
  if (isElevated) return 1;
  if (hasIssues && isLive) return 2;
  if (hasIssues) return 3;
  if (isLive) return 4;
  return 5;
}
