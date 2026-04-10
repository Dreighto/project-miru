import { DevReviewHubPage } from "@/components/dev-review-hub/DevReviewHubPage";
import { OperatorConsolePage } from "@/components/operator-console/OperatorConsolePage";
import { MiruHubPage } from "@/components/hub/MiruHubPage";

export default function App() {
  if (document.getElementById("operator-root") !== null) {
    return <OperatorConsolePage />;
  }
  if (document.getElementById("hub-root") !== null) {
    return <MiruHubPage />;
  }
  return <DevReviewHubPage />;
}
