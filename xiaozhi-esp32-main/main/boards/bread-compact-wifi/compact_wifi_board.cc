#include "wifi_board.h"
#include "codecs/no_audio_codec.h"
#include "display/oled_display.h"
#include "system_reset.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "mcp_server.h"
#include "lamp_controller.h"
#include "led/single_led.h"
#include "assets/lang_config.h"

#include <esp_log.h>
#include <driver/gpio.h>
#include <driver/i2c_master.h>
#include <driver/uart.h>
#include <esp_lcd_panel_ops.h>
#include <esp_lcd_panel_vendor.h>

#ifdef SH1106
#include <esp_lcd_panel_sh1106.h>
#endif

#define TAG "CompactWifiBoard"0

enum CarCommand : uint8_t {
    kCarCmdForward = 0x01,
    kCarCmdBackward = 0x02,
    kCarCmdTurnLeft = 0x03,
    kCarCmdTurnRight = 0x04,
    kCarCmdStop = 0x05,
    kCarCmdEmergencyStop = 0x06,
    kCarCmdForwardCm = 0x11,
    kCarCmdBackwardCm = 0x12,
};

class CompactWifiBoard : public WifiBoard {
private:
    i2c_master_bus_handle_t display_i2c_bus_;
    esp_lcd_panel_io_handle_t panel_io_ = nullptr;
    esp_lcd_panel_handle_t panel_ = nullptr;
    Display* display_ = nullptr;
    Button boot_button_;
    Button touch_button_;
    Button volume_up_button_;
    Button volume_down_button_;

    void InitializeCarUart() {
        uart_config_t uart_config = {
            .baud_rate = CAR_UART_BAUD_RATE,
            .data_bits = UART_DATA_8_BITS,
            .parity = UART_PARITY_DISABLE,
            .stop_bits = UART_STOP_BITS_1,
            .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
            .source_clk = UART_SCLK_DEFAULT,
        };

        // Force TX pin to output-high first, then switch to UART matrix.
        ESP_ERROR_CHECK(gpio_reset_pin(CAR_UART_TXD));
        ESP_ERROR_CHECK(gpio_set_direction(CAR_UART_TXD, GPIO_MODE_OUTPUT));
        ESP_ERROR_CHECK(gpio_set_level(CAR_UART_TXD, 1));

        ESP_ERROR_CHECK(uart_driver_install(CAR_UART_PORT_NUM, 1024, 0, 0, nullptr, 0));
        ESP_ERROR_CHECK(uart_param_config(CAR_UART_PORT_NUM, &uart_config));
        ESP_ERROR_CHECK(uart_set_pin(CAR_UART_PORT_NUM, CAR_UART_TXD, CAR_UART_RXD, CAR_UART_RTS, CAR_UART_CTS));
        ESP_LOGI(TAG, "Car UART initialized, tx=%d rx=%d baud=%d", CAR_UART_TXD, CAR_UART_RXD, CAR_UART_BAUD_RATE);
    }

    bool SendCarCommand(uint8_t cmd) {
        int written = uart_write_bytes(CAR_UART_PORT_NUM, &cmd, 1);
        ESP_LOGI(TAG, "Send car cmd=0x%02X written=%d", cmd, written);
        if (written != 1) {
            ESP_LOGE(TAG, "Failed to send car command, written=%d", written);
            return false;
        }
        return true;
    }

    bool SendCarForwardDistanceCm(uint16_t distance_cm) {
        uint8_t frame[5];
        frame[0] = 0xAA;
        frame[1] = kCarCmdForwardCm;
        frame[2] = (uint8_t)(distance_cm & 0xFF);
        frame[3] = (uint8_t)((distance_cm >> 8) & 0xFF);
        frame[4] = 0x55;

        int written = uart_write_bytes(CAR_UART_PORT_NUM, frame, sizeof(frame));
        ESP_LOGI(TAG, "Send car distance cmd=%u cm written=%d", distance_cm, written);
        if (written != sizeof(frame)) {
            ESP_LOGE(TAG, "Failed to send car distance command, written=%d", written);
            return false;
        }
        return true;
    }

    bool SendCarBackwardDistanceCm(uint16_t distance_cm) {
        uint8_t frame[5];
        frame[0] = 0xAA;
        frame[1] = kCarCmdBackwardCm;
        frame[2] = (uint8_t)(distance_cm & 0xFF);
        frame[3] = (uint8_t)((distance_cm >> 8) & 0xFF);
        frame[4] = 0x55;

        int written = uart_write_bytes(CAR_UART_PORT_NUM, frame, sizeof(frame));
        ESP_LOGI(TAG, "Send car backward distance cmd=%u cm written=%d", distance_cm, written);
        if (written != sizeof(frame)) {
            ESP_LOGE(TAG, "Failed to send car backward distance command, written=%d", written);
            return false;
        }
        return true;
    }

    void InitializeDisplayI2c() {
        i2c_master_bus_config_t bus_config = {
            .i2c_port = (i2c_port_t)0,
            .sda_io_num = DISPLAY_SDA_PIN,
            .scl_io_num = DISPLAY_SCL_PIN,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = 1,
            },
        };
        ESP_ERROR_CHECK(i2c_new_master_bus(&bus_config, &display_i2c_bus_));
    }

    void InitializeSsd1306Display() {
        // SSD1306 config
        esp_lcd_panel_io_i2c_config_t io_config = {
            .dev_addr = 0x3C,
            .on_color_trans_done = nullptr,
            .user_ctx = nullptr,
            .control_phase_bytes = 1,
            .dc_bit_offset = 6,
            .lcd_cmd_bits = 8,
            .lcd_param_bits = 8,
            .flags = {
                .dc_low_on_data = 0,
                .disable_control_phase = 0,
            },
            .scl_speed_hz = 400 * 1000,
        };

        ESP_ERROR_CHECK(esp_lcd_new_panel_io_i2c_v2(display_i2c_bus_, &io_config, &panel_io_));

        ESP_LOGI(TAG, "Install SSD1306 driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = -1;
        panel_config.bits_per_pixel = 1;

        esp_lcd_panel_ssd1306_config_t ssd1306_config = {
            .height = static_cast<uint8_t>(DISPLAY_HEIGHT),
        };
        panel_config.vendor_config = &ssd1306_config;

#ifdef SH1106
        ESP_ERROR_CHECK(esp_lcd_new_panel_sh1106(panel_io_, &panel_config, &panel_));
#else
        ESP_ERROR_CHECK(esp_lcd_new_panel_ssd1306(panel_io_, &panel_config, &panel_));
#endif
        ESP_LOGI(TAG, "SSD1306 driver installed");

        // Reset the display
        ESP_ERROR_CHECK(esp_lcd_panel_reset(panel_));
        if (esp_lcd_panel_init(panel_) != ESP_OK) {
            ESP_LOGE(TAG, "Failed to initialize display");
            display_ = new NoDisplay();
            return;
        }
        ESP_ERROR_CHECK(esp_lcd_panel_invert_color(panel_, false));

        // Set the display to on
        ESP_LOGI(TAG, "Turning display on");
        ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel_, true));

        display_ = new OledDisplay(panel_io_, panel_, DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);
    }

    void InitializeButtons() {
        boot_button_.OnClick([this]() {
            auto& app = Application::GetInstance();
            if (app.GetDeviceState() == kDeviceStateStarting) {
                EnterWifiConfigMode();
                return;
            }
            app.ToggleChatState();
        });
        touch_button_.OnPressDown([this]() {
            Application::GetInstance().StartListening();
        });
        touch_button_.OnPressUp([this]() {
            Application::GetInstance().StopListening();
        });

        volume_up_button_.OnClick([this]() {
            auto codec = GetAudioCodec();
            auto volume = codec->output_volume() + 10;
            if (volume > 100) {
                volume = 100;
            }
            codec->SetOutputVolume(volume);
            GetDisplay()->ShowNotification(Lang::Strings::VOLUME + std::to_string(volume));
        });

        volume_up_button_.OnLongPress([this]() {
            GetAudioCodec()->SetOutputVolume(100);
            GetDisplay()->ShowNotification(Lang::Strings::MAX_VOLUME);
        });

        volume_down_button_.OnClick([this]() {
            auto codec = GetAudioCodec();
            auto volume = codec->output_volume() - 10;
            if (volume < 0) {
                volume = 0;
            }
            codec->SetOutputVolume(volume);
            GetDisplay()->ShowNotification(Lang::Strings::VOLUME + std::to_string(volume));
        });

        volume_down_button_.OnLongPress([this]() {
            GetAudioCodec()->SetOutputVolume(0);
            GetDisplay()->ShowNotification(Lang::Strings::MUTED);
        });
    }

    // 物联网初始化，逐步迁移到 MCP 协议
    void InitializeTools() {
        static LampController lamp(LAMP_GPIO);
        auto& mcp_server = McpServer::GetInstance();

        mcp_server.AddTool("self.car.go_forward", "控制平衡车前进",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                (void)properties;
                return SendCarCommand(kCarCmdForward);
            });

        mcp_server.AddTool("self.car.go_back", "控制平衡车后退",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                (void)properties;
                return SendCarCommand(kCarCmdBackward);
            });

        mcp_server.AddTool("self.car.turn_left", "控制平衡车左转",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                (void)properties;
                return SendCarCommand(kCarCmdTurnLeft);
            });

        mcp_server.AddTool("self.car.turn_right", "控制平衡车右转",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                (void)properties;
                return SendCarCommand(kCarCmdTurnRight);
            });

        mcp_server.AddTool("self.car.stop", "控制平衡车停止", PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                (void)properties;
                return SendCarCommand(kCarCmdStop);
            });

        mcp_server.AddTool("self.car.emergency_stop", "控制平衡车急停", PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                (void)properties;
                return SendCarCommand(kCarCmdEmergencyStop);
            });

        mcp_server.AddTool("self.car.go_forward_distance", "控制平衡车前进指定距离（单位厘米）",
            PropertyList({
                Property("distance_cm", kPropertyTypeInteger, 1, 1000)
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                int distance_cm = properties["distance_cm"].value<int>();
                return SendCarForwardDistanceCm((uint16_t)distance_cm);
            });

        mcp_server.AddTool("self.car.go_back_distance", "控制平衡车后退指定距离（单位厘米）",
            PropertyList({
                Property("distance_cm", kPropertyTypeInteger, 1, 1000)
            }),
            [this](const PropertyList& properties) -> ReturnValue {
                int distance_cm = properties["distance_cm"].value<int>();
                return SendCarBackwardDistanceCm((uint16_t)distance_cm);
            });
    }

public:
    CompactWifiBoard() :
        boot_button_(BOOT_BUTTON_GPIO),
        touch_button_(TOUCH_BUTTON_GPIO),
        volume_up_button_(VOLUME_UP_BUTTON_GPIO),
        volume_down_button_(VOLUME_DOWN_BUTTON_GPIO) {
        InitializeCarUart();
        InitializeDisplayI2c();
        InitializeSsd1306Display();
        InitializeButtons();
        InitializeTools();
    }

    virtual Led* GetLed() override {
        static SingleLed led(BUILTIN_LED_GPIO);
        return &led;
    }

    virtual AudioCodec* GetAudioCodec() override {
#ifdef AUDIO_I2S_METHOD_SIMPLEX
        static NoAudioCodecSimplex audio_codec(AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_SPK_GPIO_BCLK, AUDIO_I2S_SPK_GPIO_LRCK, AUDIO_I2S_SPK_GPIO_DOUT, AUDIO_I2S_MIC_GPIO_SCK, AUDIO_I2S_MIC_GPIO_WS, AUDIO_I2S_MIC_GPIO_DIN);
#else
        static NoAudioCodecDuplex audio_codec(AUDIO_INPUT_SAMPLE_RATE, AUDIO_OUTPUT_SAMPLE_RATE,
            AUDIO_I2S_GPIO_BCLK, AUDIO_I2S_GPIO_WS, AUDIO_I2S_GPIO_DOUT, AUDIO_I2S_GPIO_DIN);
#endif
        return &audio_codec;
    }

    virtual Display* GetDisplay() override {
        return display_;
    }
};

DECLARE_BOARD(CompactWifiBoard);
