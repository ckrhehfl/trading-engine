package engine.schemas;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

/**
 * Shared Jackson configuration for schemas/** types: snake_case JSON field
 * names to match the Python (pydantic) side, and ISO-8601 timestamps
 * instead of epoch arrays. See schemas/README.md for the cross-language
 * contract this exists to satisfy.
 */
public final class SchemaObjectMapper {

    private SchemaObjectMapper() {}

    public static ObjectMapper create() {
        return new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
                .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
    }
}
