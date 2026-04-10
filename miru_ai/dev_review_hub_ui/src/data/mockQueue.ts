export type SegmentState = "empty" | "pending" | "done";

export type ReviewSegment = SegmentState;

export interface QueueVariant {
  id: string;
  label: string;
  variantKey?: string;
  imageUrl: string | null;
  miruAssetsRelPath?: string | null;
}

export interface QueueItem {
  id: string;
  cardCode?: string;
  name: string;
  setCode: string;
  cardNumber: string;
  version: string;
  segments: [
    ReviewSegment,
    ReviewSegment,
    ReviewSegment,
    ReviewSegment,
    ReviewSegment,
    ReviewSegment,
  ];
  thumbUrl?: string;
  variants: QueueVariant[];
  miruHistory?: {
    id: string;
    title: string;
    body: string;
    createdAtIso?: string;
    action?: string;
    verdict?: string;
    variantKey?: string;
  }[];
}

export const MOCK_FILTER_LABEL = "Catalog · training";
