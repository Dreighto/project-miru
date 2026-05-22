// Component test — Voyage Atlas: chart pages, chapter navigation, Voyage Log.
// Deterministic: mock data, no live Flask. E2E tests cover the real backend path.
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';

import VoyagePage from '../../src/routes/voyage/+page.svelte';

const KEYS = [
	'foosha_village', 'shells_town', 'orange_town', 'syrup_village', 'baratie',
	'cocoyasi_village', 'loguetown', 'reverse_mountain', 'whisky_peak', 'little_garden',
	'drum_island', 'alabasta', 'jaya', 'skypiea', 'long_ring_long_land', 'water_seven',
	'enies_lobby', 'thriller_bark', 'sabaody_archipelago', 'fish_man_island', 'punk_hazard',
	'dressrosa', 'zou', 'whole_cake_island', 'wano_country', 'egghead', 'elbaf'
];

const title = (k: string) =>
	k.split('_').map((w) => w[0].toUpperCase() + w.slice(1)).join(' ');

function mockData(currentKey = 'foosha_village') {
	const islands = KEYS.map((k) => ({
		key: k,
		name: title(k),
		state: (k === currentKey ? 'current' : 'fog') as 'charted' | 'current' | 'fog'
	}));
	return {
		voyage: {
			islands,
			current_island: islands.find((i) => i.state === 'current') ?? null,
			sets: [],
			voyage_log: [
				{
					kind: 'pattern' as const,
					issue_type: 'grading_dispute',
					count: 3,
					message: 'Grading Dispute — 3 recurring instances'
				}
			],
			progress: {
				sets_charted: 0,
				sets_current: 0,
				sets_fog: 0,
				sets_total: 0,
				islands_charted: 0,
				islands_fog: 26
			}
		},
		flaskDown: false as const
	};
}

describe('Voyage Atlas — chart page', () => {
	it('renders the chart', () => {
		render(VoyagePage, { props: { data: mockData() } });
		expect(screen.getByTestId('route-map')).toBeInTheDocument();
	});

	it("renders the current chapter's island nodes", () => {
		render(VoyagePage, { props: { data: mockData('foosha_village') } });
		expect(screen.getByTestId('island-node-foosha_village')).toBeInTheDocument();
		expect(screen.getByTestId('island-node-loguetown')).toBeInTheDocument();
	});

	it('opens on the chapter that holds the current island', () => {
		render(VoyagePage, { props: { data: mockData('reverse_mountain') } });
		expect(screen.getByTestId('island-node-reverse_mountain')).toBeInTheDocument();
		// East Blue islands are on a different chart page
		expect(screen.queryByTestId('island-node-foosha_village')).not.toBeInTheDocument();
	});

	it('renders the progress summary', () => {
		render(VoyagePage, { props: { data: mockData() } });
		expect(screen.getByTestId('progress-summary')).toBeInTheDocument();
	});
});

describe('Voyage Atlas — chapter navigation', () => {
	it('chapter tabs switch the active chart page', async () => {
		render(VoyagePage, { props: { data: mockData('foosha_village') } });
		expect(screen.getByTestId('island-node-foosha_village')).toBeInTheDocument();
		await fireEvent.click(screen.getByRole('button', { name: 'New World' }));
		expect(screen.getByTestId('island-node-elbaf')).toBeInTheDocument();
		expect(screen.queryByTestId('island-node-foosha_village')).not.toBeInTheDocument();
	});
});

describe('Voyage Atlas — Voyage Log panel', () => {
	it('panel is hidden until an island is tapped', () => {
		render(VoyagePage, { props: { data: mockData() } });
		expect(screen.queryByTestId('voyage-log-panel')).not.toBeInTheDocument();
	});

	it('tapping an island opens the Voyage Log', async () => {
		render(VoyagePage, { props: { data: mockData() } });
		await fireEvent.click(screen.getByTestId('island-node-loguetown'));
		expect(screen.getByTestId('voyage-log-panel')).toBeInTheDocument();
	});

	it('panel shows the selected island name', async () => {
		render(VoyagePage, { props: { data: mockData() } });
		await fireEvent.click(screen.getByTestId('island-node-baratie'));
		expect(screen.getByTestId('voyage-log-panel')).toHaveTextContent('Baratie');
	});

	it('tapping the same island again closes the panel', async () => {
		render(VoyagePage, { props: { data: mockData() } });
		await fireEvent.click(screen.getByTestId('island-node-baratie'));
		expect(screen.getByTestId('voyage-log-panel')).toBeInTheDocument();
		await fireEvent.click(screen.getByTestId('island-node-baratie'));
		expect(screen.queryByTestId('voyage-log-panel')).not.toBeInTheDocument();
	});

	it('frames a fog island as an uncharted milestone', async () => {
		render(VoyagePage, { props: { data: mockData() } });
		await fireEvent.click(screen.getByTestId('island-node-loguetown'));
		expect(screen.getByTestId('voyage-log-panel')).toHaveTextContent('Uncharted');
	});

	it("shows the ship's log on the current island", async () => {
		render(VoyagePage, { props: { data: mockData('foosha_village') } });
		await fireEvent.click(screen.getByTestId('island-node-foosha_village'));
		expect(screen.getByTestId('voyage-log-entries')).toBeInTheDocument();
		expect(screen.getByText('Grading Dispute — 3 recurring instances')).toBeInTheDocument();
	});
});

describe('Voyage Atlas — error states', () => {
	it('shows the Flask-down banner when flaskDown is true', () => {
		render(VoyagePage, { props: { data: { voyage: null, flaskDown: true } } });
		expect(screen.getByTestId('flask-down-banner')).toBeInTheDocument();
	});

	it('hides the chart when Flask is down', () => {
		render(VoyagePage, { props: { data: { voyage: null, flaskDown: true } } });
		expect(screen.queryByTestId('route-map')).not.toBeInTheDocument();
	});
});
