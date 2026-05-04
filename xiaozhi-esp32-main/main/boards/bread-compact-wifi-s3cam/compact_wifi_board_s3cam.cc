#include "wifi_board.h"
#include "codecs/no_audio_codec.h"
#include "display/lcd_display.h"
#include "system_reset.h"
#include "application.h"
#include "button.h"
#include "config.h"
#include "mcp_server.h"
#include "lamp_controller.h"
#include "led/single_led.h"
#include "esp32_camera.h"

#include <esp_log.h>
#include <driver/i2c_master.h>
#include <esp_lcd_panel_vendor.h>
#include <esp_lcd_panel_io.h>
#include <esp_lcd_panel_ops.h>
#include <driver/gpio.h>
#include <driver/uart.h>
#include <driver/spi_common.h>

#if defined(LCD_TYPE_ILI9341_SERIAL)
#include "esp_lcd_ili9341.h"
#endif

#if defined(LCD_TYPE_GC9A01_SERIAL)
#include "esp_lcd_gc9a01.h"
static const gc9a01_lcd_init_cmd_t gc9107_lcd_init_cmds[] = {
    //  {cmd, { data }, data_size, delay_ms}
    {0xfe, (uint8_t[]){0x00}, 0, 0},
    {0xef, (uint8_t[]){0x00}, 0, 0},
    {0xb0, (uint8_t[]){0xc0}, 1, 0},
    {0xb1, (uint8_t[]){0x80}, 1, 0},
    {0xb2, (uint8_t[]){0x27}, 1, 0},
    {0xb3, (uint8_t[]){0x13}, 1, 0},
    {0xb6, (uint8_t[]){0x19}, 1, 0},
    {0xb7, (uint8_t[]){0x05}, 1, 0},
    {0xac, (uint8_t[]){0xc8}, 1, 0},
    {0xab, (uint8_t[]){0x0f}, 1, 0},
    {0x3a, (uint8_t[]){0x05}, 1, 0},
    {0xb4, (uint8_t[]){0x04}, 1, 0},
    {0xa8, (uint8_t[]){0x08}, 1, 0},
    {0xb8, (uint8_t[]){0x08}, 1, 0},
    {0xea, (uint8_t[]){0x02}, 1, 0},
    {0xe8, (uint8_t[]){0x2A}, 1, 0},
    {0xe9, (uint8_t[]){0x47}, 1, 0},
    {0xe7, (uint8_t[]){0x5f}, 1, 0},
    {0xc6, (uint8_t[]){0x21}, 1, 0},
    {0xc7, (uint8_t[]){0x15}, 1, 0},
    {0xf0,
    (uint8_t[]){0x1D, 0x38, 0x09, 0x4D, 0x92, 0x2F, 0x35, 0x52, 0x1E, 0x0C,
                0x04, 0x12, 0x14, 0x1f},
    14, 0},
    {0xf1,
    (uint8_t[]){0x16, 0x40, 0x1C, 0x54, 0xA9, 0x2D, 0x2E, 0x56, 0x10, 0x0D,
                0x0C, 0x1A, 0x14, 0x1E},
    14, 0},
    {0xf4, (uint8_t[]){0x00, 0x00, 0xFF}, 3, 0},
    {0xba, (uint8_t[]){0xFF, 0xFF}, 2, 0},
};
#endif
 
#define TAG "CompactWifiBoardS3Cam"

enum CarCommand : uint8_t {
    kCmdFacetracking = 0x01,
    kCmdStopFacetracking = 0x02,
    kCmdStopInteraction = 0x03,
};

class CompactWifiBoardS3Cam : public WifiBoard {
private:
 
    Button boot_button_;
    LcdDisplay* display_;
    Esp32Camera* camera_ = nullptr;

    bool HasCameraUartPinConflict() const {
        return (CAR_UART_TXD == CAMERA_PIN_D0) || (CAR_UART_TXD == CAMERA_PIN_D1) ||
               (CAR_UART_TXD == CAMERA_PIN_D2) || (CAR_UART_TXD == CAMERA_PIN_D3) ||
               (CAR_UART_TXD == CAMERA_PIN_D4) || (CAR_UART_TXD == CAMERA_PIN_D5) ||
               (CAR_UART_TXD == CAMERA_PIN_D6) || (CAR_UART_TXD == CAMERA_PIN_D7) ||
               (CAR_UART_TXD == CAMERA_PIN_XCLK) || (CAR_UART_TXD == CAMERA_PIN_PCLK) ||
               (CAR_UART_TXD == CAMERA_PIN_VSYNC) || (CAR_UART_TXD == CAMERA_PIN_HREF) ||
               (CAR_UART_TXD == CAMERA_PIN_SIOC) || (CAR_UART_TXD == CAMERA_PIN_SIOD) ||
               (CAR_UART_RXD == CAMERA_PIN_D0) || (CAR_UART_RXD == CAMERA_PIN_D1) ||
               (CAR_UART_RXD == CAMERA_PIN_D2) || (CAR_UART_RXD == CAMERA_PIN_D3) ||
               (CAR_UART_RXD == CAMERA_PIN_D4) || (CAR_UART_RXD == CAMERA_PIN_D5) ||
               (CAR_UART_RXD == CAMERA_PIN_D6) || (CAR_UART_RXD == CAMERA_PIN_D7) ||
               (CAR_UART_RXD == CAMERA_PIN_XCLK) || (CAR_UART_RXD == CAMERA_PIN_PCLK) ||
               (CAR_UART_RXD == CAMERA_PIN_VSYNC) || (CAR_UART_RXD == CAMERA_PIN_HREF) ||
               (CAR_UART_RXD == CAMERA_PIN_SIOC) || (CAR_UART_RXD == CAMERA_PIN_SIOD);
    }

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

    bool SendRobotCommand(uint8_t cmd) {
        int written = uart_write_bytes(CAR_UART_PORT_NUM, &cmd, 1);
        ESP_LOGI(TAG, "Send robot cmd=0x%02X written=%d", cmd, written);
        if (written != 1) {
            ESP_LOGE(TAG, "Failed to send robot command, written=%d", written);
            return false;
        }
        return true;
    }

    void InitializeSpi() {
        spi_bus_config_t buscfg = {};
        buscfg.mosi_io_num = DISPLAY_MOSI_PIN;
        buscfg.miso_io_num = GPIO_NUM_NC;
        buscfg.sclk_io_num = DISPLAY_CLK_PIN;
        buscfg.quadwp_io_num = GPIO_NUM_NC;
        buscfg.quadhd_io_num = GPIO_NUM_NC;
        buscfg.max_transfer_sz = DISPLAY_WIDTH * DISPLAY_HEIGHT * sizeof(uint16_t);
        ESP_ERROR_CHECK(spi_bus_initialize(SPI3_HOST, &buscfg, SPI_DMA_CH_AUTO));
    }

    void InitializeLcdDisplay() {
        esp_lcd_panel_io_handle_t panel_io = nullptr;
        esp_lcd_panel_handle_t panel = nullptr;
        // 液晶屏控制IO初始化
        ESP_LOGD(TAG, "Install panel IO");
        esp_lcd_panel_io_spi_config_t io_config = {};
        io_config.cs_gpio_num = DISPLAY_CS_PIN;
        io_config.dc_gpio_num = DISPLAY_DC_PIN;
        io_config.spi_mode = DISPLAY_SPI_MODE;
        io_config.pclk_hz = 40 * 1000 * 1000;
        io_config.trans_queue_depth = 10;
        io_config.lcd_cmd_bits = 8;
        io_config.lcd_param_bits = 8;
        ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi(SPI3_HOST, &io_config, &panel_io));

        // 初始化液晶屏驱动芯片
        ESP_LOGD(TAG, "Install LCD driver");
        esp_lcd_panel_dev_config_t panel_config = {};
        panel_config.reset_gpio_num = DISPLAY_RST_PIN;
        panel_config.rgb_ele_order = DISPLAY_RGB_ORDER;
        panel_config.bits_per_pixel = 16;
#if defined(LCD_TYPE_ILI9341_SERIAL)
        ESP_ERROR_CHECK(esp_lcd_new_panel_ili9341(panel_io, &panel_config, &panel));
#elif defined(LCD_TYPE_GC9A01_SERIAL)
        ESP_ERROR_CHECK(esp_lcd_new_panel_gc9a01(panel_io, &panel_config, &panel));
        gc9a01_vendor_config_t gc9107_vendor_config = {
            .init_cmds = gc9107_lcd_init_cmds,
            .init_cmds_size = sizeof(gc9107_lcd_init_cmds) / sizeof(gc9a01_lcd_init_cmd_t),
        };        
#else
        ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(panel_io, &panel_config, &panel));
#endif
        
        esp_lcd_panel_reset(panel);

        esp_lcd_panel_init(panel);
        esp_lcd_panel_invert_color(panel, DISPLAY_INVERT_COLOR);
        esp_lcd_panel_swap_xy(panel, DISPLAY_SWAP_XY);
        esp_lcd_panel_mirror(panel, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y);
#ifdef  LCD_TYPE_GC9A01_SERIAL
        panel_config.vendor_config = &gc9107_vendor_config;
#endif
        display_ = new SpiLcdDisplay(panel_io, panel,
                                    DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_OFFSET_X, DISPLAY_OFFSET_Y, DISPLAY_MIRROR_X, DISPLAY_MIRROR_Y, DISPLAY_SWAP_XY);
    }

    void InitializeCamera() {
        camera_config_t config = {};
        config.pin_d0 = CAMERA_PIN_D0;
        config.pin_d1 = CAMERA_PIN_D1;
        config.pin_d2 = CAMERA_PIN_D2;
        config.pin_d3 = CAMERA_PIN_D3;
        config.pin_d4 = CAMERA_PIN_D4;
        config.pin_d5 = CAMERA_PIN_D5;
        config.pin_d6 = CAMERA_PIN_D6;
        config.pin_d7 = CAMERA_PIN_D7;
        config.pin_xclk = CAMERA_PIN_XCLK;
        config.pin_pclk = CAMERA_PIN_PCLK;
        config.pin_vsync = CAMERA_PIN_VSYNC;
        config.pin_href = CAMERA_PIN_HREF;
        config.pin_sccb_sda = CAMERA_PIN_SIOD;
        config.pin_sccb_scl = CAMERA_PIN_SIOC;
        config.sccb_i2c_port = 0;
        config.pin_pwdn = CAMERA_PIN_PWDN;
        config.pin_reset = CAMERA_PIN_RESET;
        config.xclk_freq_hz = XCLK_FREQ_HZ;
        config.pixel_format = PIXFORMAT_RGB565;
        config.frame_size = FRAMESIZE_VGA;
        config.jpeg_quality = 12;
        config.fb_count = 1;
        config.fb_location = CAMERA_FB_IN_PSRAM;
        config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
        camera_ = new Esp32Camera(config);
        camera_->SetHMirror(false);
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
    }

    //  MCP 协议
    void InitializeTools() {
        static LampController lamp(LAMP_GPIO);
        auto& mcp_server = McpServer::GetInstance();

        mcp_server.AddTool("self.face_tracking.start", "启动人脸追踪功能",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                (void)properties;
                return SendRobotCommand(kCmdFacetracking);
            });

        mcp_server.AddTool("self.face_tracking.stop", "关闭人脸追踪功能",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                (void)properties;
                return SendRobotCommand(kCmdStopFacetracking);
            });

        mcp_server.AddTool("self.active_interaction.stop", "关闭主动交互功能",
            PropertyList(),
            [this](const PropertyList& properties) -> ReturnValue {
                (void)properties;
                return SendRobotCommand(kCmdStopInteraction);
            });
    }

public:
    CompactWifiBoardS3Cam() :
        boot_button_(BOOT_BUTTON_GPIO) {
        InitializeCarUart();
        InitializeSpi();
        InitializeLcdDisplay();
        InitializeButtons();
        InitializeTools();
        if (HasCameraUartPinConflict()) {
            ESP_LOGW(TAG, "Camera disabled: UART pins (%d, %d) conflict with camera pins", CAR_UART_TXD, CAR_UART_RXD);
        } else {
            InitializeCamera();
        }
        if (DISPLAY_BACKLIGHT_PIN != GPIO_NUM_NC) {
            GetBacklight()->RestoreBrightness();
        }
        
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

    virtual Backlight* GetBacklight() override {
        if (DISPLAY_BACKLIGHT_PIN != GPIO_NUM_NC) {
            static PwmBacklight backlight(DISPLAY_BACKLIGHT_PIN, DISPLAY_BACKLIGHT_OUTPUT_INVERT);
            return &backlight;
        }
        return nullptr;
    }

    virtual Camera* GetCamera() override {
        return camera_;
    }
};

DECLARE_BOARD(CompactWifiBoardS3Cam);
