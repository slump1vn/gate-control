import type { Meta, StoryObj } from '@storybook/nextjs-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import CameraForm from './CameraForm';
import { mockCamera, mockCameraPresets, mockCameraTestFailed, mockCameraTestOk } from '@/lib/gate-mock-data';

const meta: Meta<typeof CameraForm> = {
  title: 'Gate/CameraForm',
  component: CameraForm,
  tags: ['autodocs'],
  args: {
    presets: mockCameraPresets,
    onSave: fn(async () => {}),
    onTest: fn(async () => mockCameraTestOk),
    onCancel: fn(),
  },
};

export default meta;
type Story = StoryObj<typeof CameraForm>;

/** A new camera: vendor preset paths filled in, nothing tested yet. */
export const Empty: Story = {};

/** A saved camera: "password set" indicator, stored read zone. */
export const Saved: Story = {
  args: { camera: mockCamera },
};

/** Waiting for the camera to answer the connection test. */
export const Testing: Story = {
  args: { camera: mockCamera, initialTest: { state: 'testing' }, onTest: () => new Promise(() => {}) },
};

/** Authentication failed on the snapshot step. */
export const TestFailed: Story = {
  args: { camera: mockCamera, initialTest: { state: 'done', result: mockCameraTestFailed } },
};

/** Test passed; the read zone is being drawn on the snapshot. */
export const RoiEditing: Story = {
  args: { camera: mockCamera, initialTest: { state: 'done', result: mockCameraTestOk }, initialRoiEditing: true },
};

const gateOptions = [{ id: 1, name: 'Cổng chính' }, { id: 2, name: 'Cổng tây' }];

/** Assigning the camera to a second gate as its exit camera, then saving. */
export const GateAssignment: Story = {
  args: { camera: mockCamera, gateOptions },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole('button', { name: '+ Gán vào cổng' }));
    await userEvent.selectOptions(canvas.getByLabelText('Cổng 2'), '2');
    await userEvent.selectOptions(canvas.getByLabelText('Chiều 2'), 'out');
    await userEvent.click(canvas.getByRole('button', { name: 'Lưu camera' }));
    await expect(args.onSave).toHaveBeenCalledWith(expect.objectContaining({
      gates: [{ gate: 1, direction: 'in' }, { gate: 2, direction: 'out' }],
    }));
  },
};

/** The same gate chosen twice is refused before anything is sent. */
export const GateListedTwice: Story = {
  args: { camera: mockCamera, gateOptions },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole('button', { name: '+ Gán vào cổng' }));
    await userEvent.selectOptions(canvas.getByLabelText('Cổng 2'), '1');
    await userEvent.click(canvas.getByRole('button', { name: 'Lưu camera' }));
    await expect(canvas.getByText('Một cổng bị chọn hai lần. Mỗi cổng chỉ giữ một dòng.')).toBeInTheDocument();
    await expect(args.onSave).not.toHaveBeenCalled();
  },
};

/** Without the list of gates the form cannot show the assignment, so it leaves it untouched. */
export const WithoutGateList: Story = {
  args: { camera: mockCamera },
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole('button', { name: '+ Gán vào cổng' })).toBeNull();
    await userEvent.click(canvas.getByRole('button', { name: 'Lưu camera' }));
    await expect(args.onSave).toHaveBeenCalledWith(expect.not.objectContaining({ gates: expect.anything() }));
  },
};
