import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from . import DOMAIN, CONF_CHECK_UPDATES, CONF_UPDATE_BRANCH

class SmartIRConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Xử lý config flow cho SmartIR."""
    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Bước cấu hình đầu tiên."""
        errors = {}

        if user_input is not None:
            if self._async_current_entries():
                return self.async_abort(reason="single_instance_allowed")
            return self.async_create_entry(title="SmartIR", data=user_input)

        # Cập nhật: Default Check Updates = False
        data_schema = vol.Schema({
            vol.Optional(CONF_CHECK_UPDATES, default=False): bool,
            vol.Optional(CONF_UPDATE_BRANCH, default='master'): vol.In(['master', 'rc']),
        })

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SmartIROptionsFlowHandler(config_entry)

class SmartIROptionsFlowHandler(config_entries.OptionsFlow):
    """Xử lý tùy chọn (Options)."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Lấy giá trị hiện tại, nếu chưa có thì lấy default là False
        current_check = self.config_entry.options.get(
            CONF_CHECK_UPDATES, self.config_entry.data.get(CONF_CHECK_UPDATES, False)
        )
        current_branch = self.config_entry.options.get(
            CONF_UPDATE_BRANCH, self.config_entry.data.get(CONF_UPDATE_BRANCH, 'master')
        )

        data_schema = vol.Schema({
            vol.Optional(CONF_CHECK_UPDATES, default=current_check): bool,
            vol.Optional(CONF_UPDATE_BRANCH, default=current_branch): vol.In(['master', 'rc']),
        })

        return self.async_show_form(step_id="init", data_schema=data_schema)