// Component test — Voyage page route map + Voyage Log panel (PRO-934).
// Deterministic: mock data, no live Flask. E2E tests cover the real backend path.
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';

import VoyagePage from '../../src/routes/voyage/+page.svelte';

const mockIslands = [
	{ key: 'east_blue', name: 'East Blue', state: 'charted' as const },
	{ key: 'reverse_mountain', name: 'Reverse Mountain', state: 'current' as const },
	{ key: 'whisky_peak', name: 'Whisky Peak', state: 'fog' as const },
];

const mockVoyageData = {
	islands: mockIslands,
	current_island: mockIslands[1],
	sets: [
		{
			set_code: 'OP01',
			set_name: 'Romance Dawn',
			state: 'charted' as const,
			verified_count: 100,
			total_count: 100,
		},
		{
			set_code: 'OP02',
			set_name: 'Paramount War',
			state: 'current' as const,
			verified_count: 50,
			total_count: 100,
		},
	],
	voyage_log: [
		{
			kind: 'pattern' as const,
			issue_type: 'grading_dispute',
			count: 3,
			message: 'Grading Dispute — 3 recurring instances',
		},
		{
			kind: 'alert' as const,
			issue_type: 'elevated_review',
			count: 2,
			message: '2 patterns flagged for elevated review',
		},
	],
	progress: {
		sets_charted: 1,
		sets_current: 1,
		sets_fog: 0,
		sets_total: 2,
		islands_charted: 1,
		islands_fog: 1,
	},
};

describe('Voyage page — route map', () => {
	it('renders the route map section', () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		expect(screen.getByTestId('route-map')).toBeInTheDocument();
	});

	it('renders all island nodes', () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		expect(screen.getByTestId('island-node-east_blue')).toBeInTheDocument();
		expect(screen.getByTestId('island-node-reverse_mountain')).toBeInTheDocument();
		expect(screen.getByTestId('island-node-whisky_peak')).toBeInTheDocument();
	});

	it('shows Log Pose label only on the current island', () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		const currentNode = screen.getByTestId('island-node-reverse_mountain');
		expect(currentNode).toHaveTextContent('Log Pose');
		const chartedNode = screen.getByTestId('island-node-east_blue');
		expect(chartedNode).not.toHaveTextContent('Log Pose');
	});

	it('renders progress summary', () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		expect(screen.getByTestId('progress-summary')).toBeInTheDocument();
	});
});

describe('Voyage page — Voyage Log panel', () => {
	it('panel is hidden until an island is clicked', () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		expect(screen.queryByTestId('voyage-log-panel')).not.toBeInTheDocument();
	});

	it('clicking an island opens the Voyage Log panel', async () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		await fireEvent.click(screen.getByTestId('island-node-east_blue'));
		expect(screen.getByTestId('voyage-log-panel')).toBeInTheDocument();
	});

	it('panel header shows the selected island name', async () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		await fireEvent.click(screen.getByTestId('island-node-reverse_mountain'));
		const panel = screen.getByTestId('voyage-log-panel');
		expect(panel).toHaveTextContent('Reverse Mountain');
	});

	it('clicking the same island again closes the panel', async () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		await fireEvent.click(screen.getByTestId('island-node-east_blue'));
		expect(screen.getByTestId('voyage-log-panel')).toBeInTheDocument();
		await fireEvent.click(screen.getByTestId('island-node-east_blue'));
		expect(screen.queryByTestId('voyage-log-panel')).not.toBeInTheDocument();
	});

	it('clicking a different island switches the panel', async () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		await fireEvent.click(screen.getByTestId('island-node-east_blue'));
		expect(screen.getByTestId('voyage-log-panel')).toHaveTextContent('East Blue');
		await fireEvent.click(screen.getByTestId('island-node-reverse_mountain'));
		expect(screen.getByTestId('voyage-log-panel')).toHaveTextContent('Reverse Mountain');
	});

	it('shows voyage log entries', async () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		await fireEvent.click(screen.getByTestId('island-node-east_blue'));
		expect(screen.getByTestId('voyage-log-entries')).toBeInTheDocument();
		expect(screen.getByText('Grading Dispute — 3 recurring instances')).toBeInTheDocument();
	});

	it('shows relevant sets for the selected island state', async () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		// Click the charted island — should show charted sets (OP01)
		await fireEvent.click(screen.getByTestId('island-node-east_blue'));
		expect(screen.getByTestId('set-progress')).toBeInTheDocument();
		expect(screen.getByText('Romance Dawn')).toBeInTheDocument();
	});

	it('shows uncharted message for fog islands', async () => {
		render(VoyagePage, { props: { data: { voyage: mockVoyageData, flaskDown: false } } });
		await fireEvent.click(screen.getByTestId('island-node-whisky_peak'));
		expect(screen.getByTestId('voyage-log-panel')).toHaveTextContent("Log Pose hasn't locked on");
	});
});

describe('Voyage page — error states', () => {
	it('shows Flask-down banner when flaskDown is true', () => {
		render(VoyagePage, { props: { data: { voyage: null, flaskDown: true } } });
		expect(screen.getByTestId('flask-down-banner')).toBeInTheDocument();
	});

	it('hides route map when Flask is down', () => {
		render(VoyagePage, { props: { data: { voyage: null, flaskDown: true } } });
		expect(screen.queryByTestId('route-map')).not.toBeInTheDocument();
	});
});
