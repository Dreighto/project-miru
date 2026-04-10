export interface ResourceMetric {
  key: string;
  label: string;
  value: string;
  detail: string;
  percent: number;
  available: boolean;
}

export interface HubSummary {
  catalog: {
    total_cards: number;
    total_variants: number;
    usable: boolean;
  };
  dossier: {
    dossiers_created: number;
    verified_dossiers: number;
    remaining_gaps: number;
    verified_coverage_percent: number;
    dossier_coverage_percent: number;
  };
  pipeline: {
    image_variant_analysis_count: number;
    market_prices_count: number;
    review_queue_count: number;
    publication_stage_count: number;
  };
  throughput: {
    today_reviews: number;
    total_reviews: number;
    distinct_cards_reviewed: number;
  };
  resources: ResourceMetric[];
  health: {
    issues: string[];
    issue_count: number;
  };
  restart_allowed: boolean;
  server_started_at: string;
  updated_at: string;
}
