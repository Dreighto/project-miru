import { AnimatedList } from "@/components/magicui/animated-list";
import type { QueueItem } from "@/data/mockQueue";
import { QueueRow } from "./QueueRow";

export interface QueueAnimatedListProps {
  items: QueueItem[];
  onSelectCard?: (item: QueueItem) => void;
}

export function QueueAnimatedList({ items, onSelectCard }: QueueAnimatedListProps) {
  return (
    <AnimatedList className="w-full min-w-0 gap-1.5 overflow-x-hidden pb-1">
      {items.map((item) => (
        <QueueRow key={item.id} item={item} onSelect={onSelectCard} />
      ))}
    </AnimatedList>
  );
}
