import { DevReviewHubPage } from "@/components/dev-review-hub/DevReviewHubPage";
import { MiruHubPage } from "@/components/hub/MiruHubPage";
import { OperatorConsolePage } from "@/components/operator-console/OperatorConsolePage";
import { ShadowReviewPage } from "@/components/shadow-review/ShadowReviewPage";

export default function App() {
  if (document.getElementById("shadow-review-root") !== null) {
    return <ShadowReviewPage />;
  }
  if (document.getElementById("operator-root") !== null) {
    return <OperatorConsolePage />;
  }
  if (document.getElementById("hub-root") !== null) {
    return <MiruHubPage />;
  }
  return <DevReviewHubPage />;
}
