import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, fn, within } from 'storybook/test';
import GateStatusCard from './GateStatusCard';
import {
  mockGateStatusEsp32Offline, mockGateStatusRadioDryRun, mockGateStatusRadioLive, mockGateStatusSimulated,
} from '@/lib/gate-mock-data';

const meta: Meta<typeof GateStatusCard> = {
  title: 'Gate/GateStatusCard',
  component: GateStatusCard,
  tags: ['autodocs'],
  args: { mode: 'shadow', onCommand: fn(async () => {}) },
  decorators: [(Story) => <div className="max-w-xl"><Story /></div>],
};

export default meta;
type Story = StoryObj<typeof GateStatusCard>;

export const SimulatedOpening: Story = { args: { gate: mockGateStatusSimulated } };
export const Esp32OfflineShadow: Story = { args: { gate: mockGateStatusEsp32Offline } };
export const Esp32Live: Story = { args: { gate: { ...mockGateStatusEsp32Offline, online: true }, mode: 'live' } };
export const NoDecisionYet: Story = { args: { gate: { ...mockGateStatusSimulated, last_event: null, simulator: null, arm_state: 'down' } } };

/** A gate that still only sees one direction. */
export const MissingExitCamera: Story = {
  args: { gate: { ...mockGateStatusSimulated, cameras: [mockGateStatusSimulated.cameras[0]], camera_warning: '1 of 2 cameras assigned. A gate needs one watching vehicles arriving and one watching them leave.' } },
};

/** 433 MHz controller still in dry run, on weak WiFi, before its clock has synced. */
export const RadioDryRun: Story = {
  args: { gate: mockGateStatusRadioDryRun, mode: 'live' },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText('Remote 433 MHz')).toBeInTheDocument();
    await expect(canvas.getByText('Chạy thử')).toBeInTheDocument();
    await expect(canvas.getByText(/-79 dBm · yếu/)).toBeInTheDocument();
    await expect(canvas.getByText(/Đồng hồ của bộ điều khiển chưa đồng bộ/)).toBeInTheDocument();
    await expect(canvas.getByText(/chạy thử: lệnh hợp lệ/)).toBeInTheDocument();
  },
};

/** Live over the air: the last open was transmitted, and nothing can confirm the arm moved. */
export const RadioLive: Story = {
  args: { gate: mockGateStatusRadioLive, mode: 'live' },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(/đã phát sóng \(chưa xác nhận\)/)).toBeInTheDocument();
    await expect(canvas.getByText('-61 dBm')).toBeInTheDocument();
    await expect(canvas.queryByText('Chạy thử')).not.toBeInTheDocument();
  },
};
